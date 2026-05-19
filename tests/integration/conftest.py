import asyncio
import pytest
import aiodocker
from pathlib import Path


@pytest.fixture
async def docker_client():
    """
    A real Docker client for integration tests.

    Connecting to the real Docker daemon verifies that our socket path
    and Docker client initialization are correct.
    """
    client = aiodocker.Docker()
    try:
        await client.system.info()
    except Exception as e:
        pytest.skip(f"Docker not available: {e}")
    
    yield client
    await client.close()


@pytest.fixture
async def alpine_image(docker_client: aiodocker.Docker):
    """
    Ensure the alpine image is pulled before integration tests.

    We use Alpine instead of the full Android image because it is much smaller and faster to pull.
    """
    try:
        await docker_client.images.inspect("alpine:3.19")
    except aiodocker.exceptions.DockerError:
        await docker_client.images.pull("alpine:3.19")
    return "alpine:3.19"


@pytest.fixture
async def test_registry(tmp_path, monkeypatch):
    """A fresh registry pointing at a temp directory."""
    from cleanroom.container.registry import SessionRegistry
    monkeypatch.setattr(
        "cleanroom.container.registry.REGISTRY_PATH",
        tmp_path / "sessions.json"
    )
    return SessionRegistry()
