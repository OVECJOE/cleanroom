from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import os

class Settings(BaseSettings):
    """
    All configuration for CleanRoom, read from environment variables.
    """
    
    model_config = SettingsConfigDict(
        env_prefix="CLEANROOM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # --- Docker ---
    # The Docker socket path. On Linux this is always /var/run/docker.sock.
    # It is a Unix domain socket -- a file-like IPC mechanism -- that the
    # Docker daemon listens on. When we write to this socket, we are talking
    # directly to the Docker daemon. This is what docker CLI does too.
    docker_socket: str = "unix:///var/run/docker.sock"

    # The ReDroid image to use.
    android_image: str = "redroid/redroid:12.0.0-latest"

    # --- Session limits ---
    # Maximum concurrent sessions. On a 4GB VPS, 3 is safe. 4 is possible with zRAM.
    # Above that, you are gambling with the OOM killer.
    max_sessions: int = Field(default=3, ge=1, le=10)

    # Memory limit per container in megabytes. Android Go needs at least
    # 400MB to boot reliably. 512MB gives headroom for a browser.
    session_memory_mb: int = Field(default=512, ge=400, le=2048)

    # CPU allocation per container. 1.0 means one full CPU cor.
    # On a 2-vCPU VPS with 3 sessions, that would be 3 CPUs worth of
    # allocation on 2 physical cores -- they will content, but cgroups
    # will throttle fairly rather than letting one starve the others.
    session_cpus: float = Field(default=1.0, ge=0.5, le=4.0)

    # Session TTL in seconds. 1800 = 30 minutes. After this, the session
    # is destroyed regardless of user activity.
    session_ttl_seconds: int = Field(default=1800, ge=60, le=7200)

    # --- ADB ---
    # How long to wait for Android to boot before giving up, in seconds.
    # On a warm host (image already pulled, pages cached), 45s is usually
    # enough. On a cold host (first boot), 90s might be needed.
    adb_boot_timeout: int = Field(default=90, ge=30, le=300)
    
    # How often to poll ADB to check if Android has booted, in seconds.
    adb_poll_interval: float = Field(default=2.0, ge=0.5, le=10.0)

    # --- Networking ---
    # Port range for ADB. Each session needs one port. We use the range
    # 5555-5655, which gives 100 possible concurrent sessions -- more than
    # enough for our max_sessions limit.
    adb_port_range_start: int = Field(default=5555, ge=1024, le=65000)
    adb_port_range_end: int = Field(default=5655, ge=1025, le=65535)

    # Whether to enable Tor routing for sessions. Set to False in development
    # to skip the Tor sidecar setup, which requires Tor to be installed.
    enable_tor: bool = Field(default=True)
    
    # --- Security ---
    # Secret key for signing session JWTs.
    secret_key: str = Field(default="dev-secret-change-in-production")

    # --- Observability ---
    log_level: str = Field(default="INFO")

    @property
    def session_memory_bytes(self) -> int:
        return self.session_memory_mb * 1024 * 1024
    
    @property
    def adb_nano_cpus(self) -> int:
        # Docker's CPU limiting uses nano-CPUs: 1 CPU = 1,000,000,000 nano-CPUs.
        # This odd unit is because cgroups use integer arithmetic, not floats.
        # Docker converts our float to nano-CPUs internally -- we do it explicitly
        # so there is no ambiguity.
        return int(self.session_cpus * 1_000_000_000)


settings = Settings()
