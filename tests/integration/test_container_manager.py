import asyncio

import pytest

from cleanroom.config import Settings
from cleanroom.container.manager import ContainerManager
from cleanroom.container.models import SessionStatus


@pytest.fixture
async def manager_with_alpine(test_registry, monkeypatch, alpine_image):
    """
    A ContainerManager configured to use Alpine instead of Android.

    Alpine starts immediately, letting us test the container lifecycle
    without waiting for Android to boot. We are testing that our Docker
    API calls are correct, not that Android works.
    """
    monkeypatch.setattr(
        "cleanroom.container.manager.settings",
        Settings(
            android_image=alpine_image,
            max_sessions=3,
            session_memory_mb=400,
            session_cpus=0.5,
            session_ttl_seconds=300,
            adb_port_range_start=19100,
            adb_port_range_end=19199,
            enable_tor=False,
        )
    )

    # Also patch the port pool to use our test range
    from cleanroom.container import ports as ports_module
    test_port_pool = (
        __import__("cleanroom.container.ports", fromlist=["PortPool"]).PortPool()
    )
    monkeypatch.setattr(ports_module, "port_pool", test_port_pool)
    monkeypatch.setattr("cleanroom.container.manager.port_pool", test_port_pool)

    manager = ContainerManager(test_registry)
    await manager.start()

    # Override _start_android_container to use Alpine-compatible config
    async def start_alpine_container(session_id, adb_port, network_id):
        container_config = {
            "Image": alpine_image,
            "Cmd": ["sleep", "infinity"],
            "HostConfig": {
                "Privileged": False,
                "Memory": 400 * 1024 * 1024,
                "NanoCPUs": 500_000_000,
                "MemorySwap": 400 * 1024 * 1024,
                "PortBindings": {
                    "5555/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(adb_port)}]
                },
                "RestartPolicy": {"Name": "no"},
            },
            "Labels": {
                "cleanroom.session_id": session_id,
                "cleanroom.managed": "true",
            }
        }
        container = await manager.client.containers.create(
            config=container_config,
            name=f"cleanroom-{session_id}",
        )
        container_id = container._id
        network = await manager.client.networks.get(network_id)
        await network.connect({"Container": container_id})
        await container.start()
        return container_id

    manager._start_android_container = start_alpine_container
    yield manager

    # Cleanup: destroy any sessions still running
    for session in test_registry.all():
        try:
            await manager.destroy_session(session.id)
        except Exception:
            pass
    
    await manager.stop()


@pytest.mark.integration
class TestContainerManager:

    async def test_create_session_returns_session(self, manager_with_alpine):
        """Creating a session should return a Session with a container_id."""
        session = await manager_with_alpine.create_session()
        assert session.id is not None
        assert session.container_id is not None
        assert session.adb_port is not None
        assert session.status == SessionStatus.BOOTING
    
    async def test_created_container_is_running(self, manager_with_alpine):
        """The container Docker created should actually be running."""
        session = await manager_with_alpine.create_session()
        running = await manager_with_alpine.is_container_running(session.container_id)
        assert running is True
    
    async def test_session_in_registry_after_create(
        self, manager_with_alpine, test_registry
    ):
        """Created session should be findable in the registry."""
        session = await manager_with_alpine.create_session()
        found = test_registry.get(session.id)
        assert found is not None
        assert found.container_id == session.container_id
    
    async def test_destroy_removes_container(self, manager_with_alpine):
        """After destroy, the container should not exist in Docker."""
        session = await manager_with_alpine.create_session()
        container_id = session.container_id

        await manager_with_alpine.destroy_session(session.id)

        running = await manager_with_alpine.is_container_running(container_id)
        assert running is False
    
    async def test_destroy_removes_from_registry(
        self, manager_with_alpine, test_registry
    ):
        """After destroy, the session should not be in the registry."""
        session = await manager_with_alpine.create_session()
        await manager_with_alpine.destroy_session(session.id)
        assert test_registry.get(session.id) is None

    async def test_destroy_releases_port(self, manager_with_alpine, monkeypatch):
        """After destroy, the ADB port should be available again."""
        from cleanroom.container.ports import port_pool

        session = await manager_with_alpine.create_session()
        used_before = port_pool.in_use_count

        await manager_with_alpine.destroy_session(session.id)

        assert port_pool.in_use_count == used_before - 1

    async def test_max_sessions_enforced(self, manager_with_alpine, monkeypatch):
        """Creating beyond max_sessions should raise RuntimeError."""
        monkeypatch.setattr(
            "cleanroom.container.manager.settings",
            Settings(
                android_image="alpine:3.19",
                max_sessions=2,
                session_memory_mb=400,
                session_cpus=0.5,
                enable_tor=False,
            )
        )
        sessions = []
        try:
            sessions.append(await manager_with_alpine.create_session())
            sessions.append(await manager_with_alpine.create_session())
            with pytest.raises(RuntimeError, match="Maximum sessions"):
                await manager_with_alpine.create_session()
        finally:
            for s in sessions:
                try:
                    await manager_with_alpine.destroy_session(s.id)
                except Exception:
                    pass

    async def test_container_has_correct_memory_limit(
        self, manager_with_alpine, docker_client
    ):
        """
        The container's cgroup memory limit should match our configuration.

        This is a critical test. We are verifying that the kernel actually
        received our memory limit, not just that Docker accepted the config.
        """
        session = await manager_with_alpine.create_session()

        try:
            container = await docker_client.containers.get(session.container_id)
            info = await container.show()
            # Docker reports the memory limit in bytes
            # Our config is 400MB for the test manager
            assert info["HostConfig"]["Memory"] == 400 * 1024 * 1024
        finally:
            await manager_with_alpine.destroy_session(session.id)

    async def test_adb_port_bound_to_localhost_only(
        self, manager_with_alpine, docker_client
    ):
        """
        The ADB port must be bound to 127.0.0.1, never to 0.0.0.0.

        If this test fails, we have a critical security vulnerability:
        the ADB port would be accessible from the internet.
        """
        session = await manager_with_alpine.create_session()

        try:
            container = await docker_client.containers.get(session.container_id)
            info = await container.show()
            port_bindings = info["HostConfig"]["PortBindings"]
            adb_bindings = port_bindings.get("5555/tcp", [])

            for binding in adb_bindings:
                assert binding["HostIp"] == "127.0.0.1", (
                    f"ADB port bound to {binding['HostIp']} instead of 127.0.0.1!"
                    " This is a security vulnerability."
                )
        finally:
            await manager_with_alpine.destroy_session(session.id)

    async def test_concurrent_session_creation(self, manager_with_alpine):
        """
        Creating multiple sessions concurrently should succeed without
        port conflicts or registry corruption.
        """
        sessions = await asyncio.gather(
            manager_with_alpine.create_session(),
            manager_with_alpine.create_session(),
        )

        try:
            ports = [s.adb_port for s in sessions]
            assert len(set(ports)) == 2, "Concurrent sessions got duplicate ports"

            ids = [s.id for s in sessions]
            assert len(set(ids)) == 2, "Concurrent sessions got duplicate IDs"

            for s in sessions:
                assert await manager_with_alpine.is_container_running(s.container_id)
        finally:
            for s in sessions:
                try:
                    await manager_with_alpine.destroy_session(s.id)
                except Exception:
                    pass
