import asyncio
import logging
from contextlib import asynccontextmanager

import aiodocker
import aiodocker.exceptions

from cleanroom.config import settings
from cleanroom.container.models import Session, SessionStatus
from cleanroom.container.network import create_session_network, destroy_session_network
from cleanroom.container.ports import port_pool
from cleanroom.container.registry import SessionRegistry

logger = logging.getLogger(__name__)

# The tmpfs options for the Android /data directory. This is where apps store their data, so it needs to be writable.
ANDROID_DATA_TMPFS = {
    "type": "tmpfs",
    "target": "/data",
    "tmpfs": {
        "Size": 400 * 1024 * 1024, # 400MB in bytes
        "Mode": 0o755,
    }
}


class ContainerManager:
    """
    Manages the lifecycle of CleanRoom Android containers.

    This is the control plane for sessions. It creates, monitors,
    and destroys containers, maintaining the registry as the authoritative
    record of what is running.

    The Docker client (aiodocker) speaks the Docker Engine API over Unix socket at /var/run/docker.sock.
    Every method call here is an HTTP request to that API, wrapped in asyncio so it
    does not block.
    """

    def __init__(self, registry: SessionRegistry):
        self._registry = registry
        self._client: aiodocker.Docker | None = None
    
    async def start(self) -> None:
        """Initialize the Docker client. Called at application startp"""
        self._client = aiodocker.Docker(url=settings.docker_socket)
        # Ping Docker to verify the connection. If this fails, the daemon
        # is not running or the socket is not accessible.
        try:
            await self._client.system.info()
            logger.info("Connected to Docker daemon at %s", settings.docker_socket)
        except Exception as e:
            logger.critical("Cannot connect to Docker: %s", e)
            raise
    
    async def stop(self) -> None:
        """Close the Docker client. Called at application shutdown."""
        if self._client:
            await self._client.close()
    
    @property
    def client(self) -> aiodocker.Docker:
        if self._client is None:
            raise RuntimeError("ContainerManager not started")
        return self._client
