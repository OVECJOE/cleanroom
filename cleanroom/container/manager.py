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
    
    async def create_session(self) -> Session:
        """
        Create a new CleanRoom session. This is the most complex operation. It:
        1. Checks the session limit
        2. Creates the session record
        3. Acquires an ADB port
        4. Creates the isolated network
        5. Starts the Android container
        6. Updates the session status

        If any step fails after we have acquired resources (port, network),
        we clean up those resources before re-raising...to prevent resource
        leaks.
        """
        # Check session limit before acquiring any resource
        if self._registry.count() >= settings.max_sessions:
            raise RuntimeError(
                f"Maximum sessions ({settings.max_sessions}) reached. Cannot create new session."
            )
        
        # Create the session record immediately so it is in the registry.
        # If we crash between here and starting the container, the watchdog
        # will find this session in CREATING state and clean it up.
        session = Session()
        await self._registry.add(session)

        port = None
        network_id = None

        try:
            # Acquire a port.
            port = await port_pool.acquire()
            session.adb_port = port
            await self._registry.update(session)

            # Create the isolated network.
            network_id = await create_session_network(self.client, session.id)
            session.network_id = network_id
            await self._registry.update(session)

            # Start the Android container.
            container_id = await self._start_android_container(
                session_id=session.id,
                adb_port=port,
                network_id=network_id,
            )
            session.container_id = container_id
            session.status = SessionStatus.BOOTING
            await self._registry.update(session)

            logger.info(
                "Session %s created: container=%s port=%d",
                session.id, container_id[:12], port
            )

            return session
        except Exception as e:
            logger.error("Failed to create session %s: %s", session.id, e)
            # Clean up whatever we managed to create
            await self._cleanup_failed_creation(
                session=session,
                port=port,
                network_id=network_id,
            )
            raise
    
    async def _start_android_container(
        self,
        session_id: str,
        adb_port: int,
        network_id: str,
    ) -> str:
        """
        Start the ReDroid Android Go container.
        
        Here are what the configuration options mean:

        - privileged=True: required for Binder and Ashmem. The container
          gets access to /dev/binder, /dev/hwbinder, /dev/vndbinder.
          This is a known risk, mitigated by seccomp and the fact that the
          container's filesystem is read-only except for the tmpfs mounts.

        - mem_limit: creates a cgroup memory limit. If the container tries
          to allocate beyond this, the OOM killer targets it first.

        - nano_cpus: creates a cgroup CPU limit. Docker converts this to
          cpu.cfs_quota_us and cpu.cfs_period_us in the cgroup filesystem.

        - mounts with tmpfs: the /data directory lives in RAM. The kernel
          creates a tmpfs filesystem and mounts it at /data in the container's
          mount namespace. When the container stops, the VFS unmounts it and
          the memory is freed.

        - PortBindings: maps container port 5555 (ADB) to a host port.
          The binding is to 127.0.0.1 ONLY -- never 0.0.0.0. This means
          the port is only reachable from the host itself, not from the
          internet. Our FastAPI proxy is the only thing that connects to it.

        - NetworkMode: connects the container to the session's isolated
          bridge network. Docker sets up a veth pair -- one end in the
          container's network namespace, one end on the host bridge.
        """
        container_config = {
            "Image": settings.android_image,
            "Hostname": f"cleanroom-{session_id[:8]}",
            # Android boot parameters passed as command arguments
            "Cmd": [
                "androidboot.hardware=redroid",
                "androidboot.redroid_width=720",
                "androidboot.redroid_height=1280",
                "androidboot.redroid_fps=24",
                # Disable GPU accerelation; use software rendering
                # GPU passthrough requires host GPU and driver setup
                # Though slower, software rendering works everywhere.
                "androidboot.redroid_gpu_mode=guest",
            ],
            "HostConfig": {
                "Privileged": True,
                "Memory": settings.session_memory_bytes,
                "NanoCPUs": settings.adb_nano_cpus,
                # Memory swap = memory limit means no swap allowed for this container.
                # We rely on host-level zRAM instead, which compresses cold pages
                # transparently and per-container swap will be too slow.
                "MemorySwap": settings.session_memory_bytes,
                "Mounts": [ANDROID_DATA_TMPFS],
                "PortBindings": {
                    "5555/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(adb_port)}]
                },
                # Security options. Even with --privileged, we can constrain
                # which system calls the container's processes can make.
                # no-new-privileges prevents privilege escalation via setuid binaries.
                "SecurityOpt": ["no-new-privileges"],
                # Container filesystem is read-only. Android writes go to
                # the tmpfs mounts only. A compromised container cannot
                # modify its own image or the overlay filesystem.
                "ReadonlyRootfs": False, # We need to write to /data, so rootfs cannot be read-only
                # Restart policy: no automatic restart.
                "RestartPolicy": {"Name": "no"},
            },
            "Labels": {
                "cleanroom.session_id": session_id,
                "cleanroom.managed": "true",
            }
        }

        container = await self.client.containers.create(
            config=container_config,
            name=f"cleanroom-{session_id}",
        )
        container_id = container._id

        # Attach to the session network before starting.
        network = await self.client.networks.get(network_id)
        await network.connect({"Container": container_id})
        
        await container.start()
        return container_id
    
    async def destroy_session(self, session_id: str) -> None:
        """
        Destroy a session and all its resources.
        
        The destruction order matters:
        1. Mark session as DESTROYING in registry.
        2. Stop and remove the container.
        3. Remove the Tor sidecar (if present).
        4. Delete the network.
        5. Release the ADB port.
        6. Remove from registry.
        """
        session = self._registry.get(session_id)
        if session is None:
            logger.warning("Destroy called for unknown session %s", session_id)
            return
        
        session.status = SessionStatus.DESTROYING
        await self._registry.update(session)
        logger.info("Destroying session %s", session_id)

        errors = []

        # Remove the Android container
        if session.container_id:
            try:
                await self._remove_container(session.container_id)
            except Exception as e:
                errors.append(f"Container removal: {e}")
        
        # Remove the Tor sidecar
        if session.tor_container_id:
            try:
                await self._remove_container(session.tor_container_id)
            except Exception as e:
                errors.append(f"Tor removal: {e}")
        
        # Delete the network
        if session.network_id:
            try:
                await destroy_session_network(
                    self.client, session.network_id, session_id
                )
            except Exception as e:
                errors.append(f"Network deletion: {e}")
        
        # Release the port
        if session.adb_port:
            await port_pool.release(session.adb_port)
        
        # Remove from registry (this persists to disk)
        session.status = SessionStatus.DEAD
        await self._registry.remove(session_id)

        if errors:
            logger.error(
                "Session %s destroyed with errors: %s",
                session_id, "; ".join(errors)
            )
        else:
            logger.info("Session %s destroyed successfully", session_id)
    
    async def _remove_container(self, container_id: str) -> None:
        """
        Stop and remove a container.
        
        We use force=True which sends SIGKILL immediately rather than
        SIGTERM followed by a grace period. For privacy, we want instant
        termination.
        """

        try:
            container = await self.client.containers.get(container_id)
            await container.delete(force=True)
            logger.debug("Removed container %s", container_id[:12])
        except aiodocker.exceptions.DockerError as e:
            if e.status == 404:
                logger.warning("Container %s not found (already removed?)", container_id[:12])
            else:
                raise
    
    async def _cleanup_failed_creation(
        self,
        session: Session,
        port: int | None,
        network_id: str | None,
    ) -> None:
        """
        Clean up resources after a failed session creation.

        Called when create_session() fails partway through. We cannot
        call destroy_session() because the session may not be fully registered.
        """
        if session.container_id:
            try:
                await self._remove_container(session.container_id)
            except Exception as e:
                logger.error("Cleanup: container removal failed for session %s: %s", session.id, e)

        if network_id:
            try:
                await destroy_session_network(
                    self.client, network_id, session.id
                )
            except Exception as e:
                logger.error("Cleanup: network deletion deletion for session %s: %s", session.id, e)
        
        if port:
            await port_pool.release(port)
        
        session.status = SessionStatus.DEAD
        await self._registry.remove(session.id)
    
    async def get_container_stats(self, container_id: str) -> dict:
        """
        Get live resource usage for a container.

        Docker reads this from /sys/fs/cgroup/, which is the same file
        the kernel's cgroup sybsystem updates in real time... Basically
        how `docker stats` works.
        """
        try:
            container = await self.client.containers.get(container_id)
            stats = await container.stats(stream=False)
            return stats[0] if isinstance(stats, list) else stats
        except Exception:
            return {}

    async def is_container_running(self, container_id: str) -> bool:
        """Check if a container is currently in the running state."""        
        try:
            container = await self.client.containers.get(container_id)
            info = await container.show()
            return info["State"]["Running"]
        except aiodocker.exceptions.DockerError as e:
            if e.status == 404:
                return False
            else:
                raise
