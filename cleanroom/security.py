"""
Security validation run at startup and periodically.

These checks verify that the security-critical parts of the deployment
are configured correctly. A wrong configuration that passes all other
tests might still be a security hole; aims to prevent that.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def verify_docker_socket_permissions() -> None:
    """The Docker socket should not be world-writable."""
    socket_path = Path("/var/run/docker.sock")
    if not socket_path.exists():
        return
    
    stat = socket_path.stat()
    mode = stat.st_mode & 0o777

    if mode & 0o002: # world-writable
        raise RuntimeError(
            f"SECURITY: Docker socket {socket_path} is world-writable"
            f" (mode={oct(mode)}). "
            "This allows privilege escalation. Fix: chmod 660 /var/run/docker.sock"
        )


def verify_not_running_as_root() -> None:
    """The application should not run as root."""
    if os.getuid() == 0:
        logger.warning(
            "SECURITY WARNING: Running as root. "
            "Create a dedicated user and run as that user in production."
            "The setup.sh script does this automatically."
        )


def verify_binder_devices() -> None:
    """
    Verify Binder devices are accessible as without them, Android containers
    will fail to boot
    """
    required = ["/dev/binder", "/dev/hwbinder", "/dev/vndbinder"]
    missing = [d for d in required if not Path(d).exists()]

    if missing:
        raise RuntimeError(
            f"Android kernel modules not loaded. Missing: {missing}. "
            f"Run: modprobe binder_linux devices=binder,hwbinder,vndbinder"
        )


def verify_zram_configured() -> bool:
    """
    Check if zRAM is configured. Not fatal if missing, but log a warning
    because memory will be tight on a 4GB VPS without it.
    """
    zram_active = Path("/dev/zram0").exists()
    if not zram_active:
        logger.warning(
            "zRAM not configured. Memory headroom on a 4GB VPS will be tight "
            "with 3+ concurrent sessions. Enable zRAM for production: "
            "systemctl enable --now zram.service"
        )
    return zram_active


def run_startup_security_checks() -> None:
    """
    Run all security checks. Called once at application startup.

    Fatal checks (raise RuntimeError): Docker socket permissions, Binder devices.
    Warning checks (log warning): root user, zRAM.
    """
    logger.info("Running startup security checks...")

    verify_not_running_as_root()

    try:
        verify_docker_socket_permissions()
    except RuntimeError as e:
        logger.error(str(e))
        raise

    try:
        verify_binder_devices()
    except RuntimeError as e:
        logger.warning(
            "Binder check failed: %s. Android containers will not boot.", e
        )
        # Do not raise -- allow the backend to start even without Binder.
        # This lets the API run in environments without Android (e.g., CI)
        # and return meaningful errors when sessions are requested.

    verify_zram_configured()

    logger.info("Security checks complete")
