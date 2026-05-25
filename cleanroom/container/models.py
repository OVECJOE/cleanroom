import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SessionStatus(StrEnum):
    """
    The lifecycle status of a session.

    CREATING: container is being set up. Not yet usable.
    BOOTING: container exists, Android is starting. Still not usable.
    READY: Android booted, ADB connected, stream available.
    DESTROYING: teardown in progress.
    DEAD: container removed. Terminal state.

    This state machine matters because the API needs to know what to tell
    a user who asks about a session that is still booting vs one that is
    ready. And the watchdog needs to know which sessions to enforce TTL on.
    """

    CREATING = "creating"
    BOOTING = "booting"
    READY = "ready"
    DESTROYING = "destroying"
    DEAD = "dead"


class Session(BaseModel):
    """
    The complete state of a CleanRoom session.

    This model is the single source of truth for everything we know about
    a running session. It is serialized to JSON in the registry file and
    deserialized on startup for crash recovery.

    Every field has a reason:
    - id: unique identifier, used in all API paths
    - container_id: Docker's identifier for the container. We need this to
      call docker.containers.get() -- Docker does not know about our session IDs.
    - network_id: Docker's identifier for the session's isolated bridge network.
      We delete this separately from the container on teardown.
    - tor_container_id: Docker's identifier for the Tor sidecar, if enabled.
    - adb_port: the host port bound to the container's ADB port 5555.
      Bound to 127.0.0.1 only -- never exposed publicly.
    - created_at: used by the watchdog to enforce TTL.
    - expires_at: pre-calculated expiry time. If the backend crashes and
      restarts, we compare expires_at to now() -- if expired, destroy.
    - status: current lifecycle state.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    container_id: str | None = None
    network_id: str | None = None
    tor_container_id: str | None = None
    adb_port: int | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    expires_at: datetime | None = None
    status: SessionStatus = SessionStatus.CREATING
    error: str | None = None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at
    
    @property
    def age_seconds(self) -> float:
        return (datetime.now(UTC) - self.created_at).total_seconds()

    def model_post_init(self, __context) -> None:
        """Calculate expires_at from config if not set."""
        from cleanroom.config import settings
        if self.expires_at is None and self.created_at is not None:
            from datetime import timedelta
            self.expires_at = self.created_at + timedelta(
                seconds=settings.session_ttl_seconds
            )
