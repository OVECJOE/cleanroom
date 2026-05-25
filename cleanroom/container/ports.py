import asyncio
import logging
import socket

from cleanroom.config import settings

logger = logging.getLogger(__name__)


class PortPool:
    """
    A thread-safe pool of ADB port numbers.

    Every session needs a port. We pre-allocate the pool for the configured
    range and hand them out one at a time. When a session ends, its port goes
    back in the pool.
    """

    def __init__(self):
        self._available: set[int] = set(
            range(
                settings.adb_port_range_start,
                settings.adb_port_range_end + 1
            )
        )
        self._in_use: set[int] = set()
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> int:
        """
        Claim the next available port.

        We verify the port is not already bound on the host before handing it
        out, because something external might have grabbed a port in our range.

        Raises RuntimeError if no ports are available.
        """
        async with self._lock:
            for port in sorted(self._available):
                if self._is_port_free(port):
                    self._available.discard(port)
                    self._in_use.add(port)
                    logger.debug("Acquired ADB port %d", port)
                    return port
            raise RuntimeError(
                f"No available ADB ports in range "
                f"{settings.adb_port_range_start}-{settings.adb_port_range_end}"
            )
    
    async def release(self, port: int) -> None:
        async with self._lock:
            self._in_use.discard(port)
            self._available.add(port)
            logger.debug("Released ADB port %d", port)

    async def reclaim(self, ports: list[int]) -> None:
        """Reclaim ports from recovered sessions on startup."""
        async with self._lock:
            for port in ports:
                self._available.discard(port)
                self._in_use.add(port)
    
    @staticmethod
    def _is_port_free(port: int) -> bool:
        """Check if a port is free by attempting to bind to it."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False
    
    @property
    def available_count(self) -> int:
        return len(self._available)
    
    @property
    def in_use_count(self) -> int:
        return len(self._in_use)


port_pool = PortPool()
