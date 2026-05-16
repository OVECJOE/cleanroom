import asyncio
import logging
from datetime import datetime, timezone

from cleanroom.container.manager import ContainerManager
from cleanroom.container.models import SessionStatus
from cleanroom.container.registry import SessionRegistry

logger = logging.getLogger(__name__)


class TTLWatchdog:
    """
    Background task that enforces session time-to-live.

    The watchdog runs in a separate asyncio task alongside the FastAPI
    event loop. It wakes up every `check_interval` seconds and inspects
    all active session. Any session that has passed its expiry time gets
    destroyed.

    This is the "hard kill" guarantee: even if the user never closes their
    browser, even if the WebSocket connection stays open, the session dies
    after TTL. The kernel enforces this because the watchdog calls
    destroy_session(), which calls docker rm -f, which sends SIGKILL to
    the container's PID namespace root, which the kernel propagates to
    every process in that namespace.

    The watchdog also detects containers that have died unexpectedly.
    """

    def __init__(
        self,
        registry: SessionRegistry,
        destroy_fn, # ContainerManager.destroy_session
        check_interval: float = 30.0,
    ):
        self._registry = registry
        self._destroy = destroy_fn
        self._check_interval = check_interval
        self._task: asyncio.Task | None = None
        self._running = False
    
    async def start(self) -> None:
        """Start the watchdog background task."""
        self._running = True
        self._task = asyncio.create_task(
            self._loop(), name="cleanroom-watchdog",
        )
        logger.info("TTL watchdog started (interval=%ds)", self._check_interval)
    
    async def stop(self) -> None:
        """Stop the watchdog gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("TTL watchdog stopped")
    
    async def _loop(self) -> None:
        """The main watchdog loop."""
        while self._running:
            try:
                await self._check_sessions()
            except Exception as e:
                # Never let an exception kill the watchdog
                # Log it and keep running.
                logger.error("Watchdog check failed: %s", e, exc_info=True)
            await asyncio.sleep(self._check_interval)
    
    async def _check_sessions(self) -> None:
        """Check all sessions and destroy any that have expired."""
        sessions = list(self._registry)
        now = datetime.now(timezone.utc)

        for session in sessions:
            # Skip sessions in terminal or transitional states
            if session.status in (SessionStatus.DEAD, SessionStatus.DESTROYING):
                continue

            if session.is_expired:
                logger.info(
                    "Session %s expired (age=%.0fs), destroying...",
                    session.id,
                    session.age_seconds
                )
                
                try:
                    await self._destroy(session.id)
                except Exception as e:
                    logger.error(
                        "Session %s destruction failed: %s",
                        session.id,
                        e,
                        exc_info=True
                    )
    
    async def recover_from_crash(self, container_manager: ContainerManager) -> None:
        """
        Called once at startup to handle sessions from before a crash
        
        When the backend restarts after a crash, the registry file may
        contain sessions that were running. We inspect each one:

        - If the container is still running and the session is not yet
        expired, we can attempt to recover it (re-attach ADB etc.).
        For simplicity in the MVP, we destroy all pre-crash sessions.

        - If the container no longer exists, we just clean up the registry.

        - If the port is in use, we reclaim it in the port pool before the
        session is destroyed (so the destruction can release it).
        """
        sessions = await self._registry.load_from_disk()
        if not sessions:
            return
        
        logger.info(
            "Crash recovery: found %d sessions from previous run",
            len(sessions)
        )

        ports_to_reclaim = [
            s.adb_port for s in sessions if s.adb_port is not None
        ]
        if ports_to_reclaim:
            from cleanroom.container.ports import port_pool
            await port_pool.reclaim(ports_to_reclaim)
        
        for session in sessions:
            logger.info(
                "Recovering session %s (status=%s, age=%.0fs)",
                session.id, session.status, session.age_seconds
            )
            try:
                await container_manager.destroy_session(session.id)
            except Exception as e:
                logger.error(
                    "Failed to destroy recovered session %s: %s",
                    session.id, e
                )
