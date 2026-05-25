import pytest
from httpx import ASGITransport, AsyncClient

from cleanroom.config import Settings
from cleanroom.main import create_application


@pytest.fixture
async def test_app(tmp_path, monkeypatch, alpine_image):
    """
    A full FastAPI application instance for API integration testing.

    We override config to use Alpine, short TTL, and test port ranges
    so these tests are fast and do not interfere with production settings.
    """
    monkeypatch.setattr(
        "cleanroom.container.registry.REGISTRY_PATH",
        tmp_path / "sessions.json"
    )

    test_settings = Settings(
        android_image=alpine_image,
        max_sessions=3,
        session_memory_mb=400,
        session_cpus=0.5,
        session_ttl_seconds=60,
        adb_port_range_start=19200,
        adb_port_range_end=19299,
        enable_tor=False,
    )
    monkeypatch.setattr("cleanroom.config.settings", test_settings)
    monkeypatch.setattr("cleanroom.container.manager.settings", test_settings)
    monkeypatch.setattr("cleanroom.main.settings", test_settings)

    # Override _start_android_container to use Alpine-compatible config
    from cleanroom.container.manager import ContainerManager
    async def start_alpine_container(self, session_id, adb_port, network_id):
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
        container = await self.client.containers.create(
            config=container_config,
            name=f"cleanroom-{session_id}",
        )
        container_id = container._id
        network = await self.client.networks.get(network_id)
        await network.connect({"Container": container_id})
        await container.start()
        return container_id

    monkeypatch.setattr(
        ContainerManager, "_start_android_container", start_alpine_container
    )

    monkeypatch.setenv("CLEANROOM_REGISTRY_PATH", str(tmp_path / "sessions.json"))

    app = create_application()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        # The lifespan starts when we enter the app context.
        async with app.router.lifespan_context(app):
            yield client, app


@pytest.mark.integration
class TestHealthEndpoint:

    async def test_health_returns_200(self, test_app):
        client, _ = test_app
        response = await client.get("/health")
        assert response.status_code == 200
    
    async def test_health_shows_docker_connected(self, test_app):
        client, _ = test_app
        response = await client.get("/health")
        data = response.json()
        assert data["docker_connected"] is True
    
    async def test_health_shows_zero_sessions_initially(self, test_app):
        client, _ = test_app
        response = await client.get("/health")
        data = response.json()
        assert data["active_sessions"] == 0


@pytest.mark.integration
class TestSessionAPI:

    async def test_create_session_returns_201(self, test_app):
        client, _ = test_app
        response = await client.post("/api/sessions")
        assert response.status_code == 201
        data = response.json()
        assert "session_id" in data
        assert "stream_url" in data
        # Cleanup
        await client.delete(f"/api/sessions/{data['session_id']}")
    
    async def test_created_session_appears_in_health(self, test_app):
        client, _ = test_app
        create_resp = await client.post("/api/sessions")
        session_id = create_resp.json()["session_id"]

        try:
            health_resp = await client.get("/health")
            assert health_resp.json()["active_sessions"] == 1
        finally:
            await client.delete(f"/api/sessions/{session_id}")

    async def test_get_session_returns_session(self, test_app):
        client, _ = test_app
        create_resp = await client.post("/api/sessions")
        session_id = create_resp.json()["session_id"]

        try:
            get_resp = await client.get(f"/api/sessions/{session_id}")
            assert get_resp.status_code == 200
            data = get_resp.json()
            assert data["session_id"] == session_id
        finally:
            await client.delete(f"/api/sessions/{session_id}")

    async def test_get_nonexistent_session_returns_404(self, test_app):
        client, _ = test_app
        response = await client.get("/api/sessions/does-not-exist")
        assert response.status_code == 404

    async def test_delete_session_returns_204(self, test_app):
        client, _ = test_app
        create_resp = await client.post("/api/sessions")
        session_id = create_resp.json()["session_id"]

        delete_resp = await client.delete(f"/api/sessions/{session_id}")
        assert delete_resp.status_code == 204

    async def test_delete_removes_session(self, test_app):
        client, _ = test_app
        create_resp = await client.post("/api/sessions")
        session_id = create_resp.json()["session_id"]
        await client.delete(f"/api/sessions/{session_id}")

        get_resp = await client.get(f"/api/sessions/{session_id}")
        assert get_resp.status_code == 404

    async def test_delete_nonexistent_returns_404(self, test_app):
        client, app = test_app
        response = await client.delete("/api/sessions/ghost-session")
        assert response.status_code == 404

    async def test_session_count_updates_correctly(self, test_app):
        client, _ = test_app
        sessions = []

        try:
            for _ in range(2):
                resp = await client.post("/api/sessions")
                sessions.append(resp.json()["session_id"])

            health = await client.get("/health")
            assert health.json()["active_sessions"] == 2

            await client.delete(f"/api/sessions/{sessions[0]}")
            sessions.pop(0)

            health = await client.get("/health")
            assert health.json()["active_sessions"] == 1
        finally:
            for sid in sessions:
                try:
                    await client.delete(f"/api/sessions/{sid}")
                except Exception:
                    pass
