import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from cleanroom.container.models import Session, SessionStatus

logger = logging.getLogger(__name__)

# The registry file lives in /var/lib/cleanroom/ in production,
# or a local directory in development. This path is used for crash recovery.
REGISTRY_PATH = Path(
    os.environ.get("CLEANROOM_REGISTRY_PATH", "/var/lib/cleanroom/sessions.json")
)


class SessionRegistry:
    """
    An in-memory store for active sessions, with persistence for crash recovery.

    Think of this as the landlord's logbook. Every tenant (session) gets
    an entry the moment they move in (container created), and the entry
    is removed when they leave (container destroyed). If the landlord has
    a heart attack (backend crashes), someone else can pick up the logbook
    and know exactly which apartments are occupied and when their leases
    expire.

    Thread safety: all mutation goes through asyncio. Since FastAPI runs
    on a single-threaded event loop, there are no concurrent writes as
    each coroutine runs to the next await point before another starts.
    The lock is here as defense-in-depth for future parellelism.
    """

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
    
    async def add(self, session: Session) -> None:
        async with self._lock:
            self._sessions[session.id] = session
            await self._persist()
    
    async def update(self, session: Session) -> None:
        async with self._lock:
            if session.id not in self._sessions:
                raise KeyError(f"Session {session.id} not in registry.")
            self._sessions[session.id] = session
            await self._persist()
    
    async def remove(self, session_id: str) -> Session | None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            await self._persist()
            return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)
    
    def all(self) -> list[Session]:
        return list(self._sessions.values())
    
    def count(self) -> int:
        return len(self._sessions)
    
    def active(self) -> list[Session]:
        return [
            s for s in self._sessions.values()
            if s.status == SessionStatus.READY
        ]
    
    def __iter__(self) -> Iterator[Session]:
        return iter(self._sessions.values())
    
    async def _persist(self) -> None:
        """Write the current state to disk atomically."""
        try:
            REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                sid: session.model_dump(mode="json")
                for sid, session in self._sessions.items()
            }
            # Write to temp file in same directory (same filesystem, so
            # rename is guaranteed atomic)
            fd, tmp_path = tempfile.mkstemp(
                dir=REGISTRY_PATH.parent,
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, default=str, indent=2)
                os.rename(tmp_path, REGISTRY_PATH)
            except Exception:
                os.unlink(tmp_path)
                raise
        except Exception as e:
            # Persistence failure is not fatal -- we log it but do not crash.
            # The in-memory state is still correct. We lose crash recovery
            # for this update, but the session still works.
            logger.error("Failed to persist registry: %s", e)
    
    async def load_from_disk(self) -> list[Session]:
        """
        Load sessions from the registry file on startup.

        Called once at startup. Returns the list of sessions that were
        persisted before the last shutdown/crash. The caller (the watchdog)
        will inspect each one and decide whether to recover or destroy it.
        """
        if not REGISTRY_PATH.exists():
            logger.info("No registry file found at %s, starting fresh.", REGISTRY_PATH)
            return []

        try:
            data = json.loads(REGISTRY_PATH.read_text())
            sessions = []
            for sid, raw in data.items():
                try:
                    session = Session.model_validate(raw)
                    self._sessions[sid] = session
                    sessions.append(session)
                except Exception as e:
                    logger.warning("Could not deserialize session %s: %s", sid, e)
            logger.info("Loaded %d sessions from registry.", len(sessions))
            return sessions
        except Exception as e:
            logger.error("Failed to load registry from disk: %s", e)
            return []
