import asyncio
import pytest
import subprocess
import shutil

from cleanroom.config import settings


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "e2e: end-to-end tests requiring KVM, Android kernel modules, and the ReDroid image"
    )


@pytest.fixture(scope="session", autouse=True)
def check_e2e_prerequisites():
    """
    Verify that the host is configured for Android-in-Docker.

    These checks run once before all E2E tests. If any fail,
    the tests are skipped, because the environment is not set up,
    which is a configuration issue, not a code bug.
    """
    missing = []

    # Check for KVM
    import os
    if not os.path.exists("/dev/kvm"):
        missing.append("/dev/kvm not found (KVM required for ReDroid)")
    
    # Check for Binder
    if not os.path.exists("/dev/binder"):
        missing.append(
            "/dev/binder not found. Load: modprobe binder_linux devices=binder,hwbinder,vndbinder"
        )
    
    # Check for Ashmem
    if not os.path.exists("/dev/ashmem"):
        missing.append(
            "/dev/ashmem not found. Load: modprobe ashmem_linux"
        )
    
    # Check for ADB
    if not shutil.which("adb"):
        missing.append("adb not found. Install: apt install android-tools-adb")
    
    if missing:
        pytest.skip(
            "E2E prerequisites not met:\n" + "\n".join(f"   - {m}" for m in missing)
        )


@pytest.fixture(scope="session")
async def e2e_manager(tmp_path_factory):
    """A ContainerManager using the real Android image for E2E tests."""
    from cleanroom.container.registry import SessionRegistry
    from cleanroom.container.manager import ContainerManager

    tmp_path = tmp_path_factory.mktemp("e2e_registry")
    import os
    os.environ["CLEANROOM_REGISTRY_PATH"] = str(tmp_path / "sessions.json")

    registry = SessionRegistry()
    manager = ContainerManager(registry)
    await manager.start()

    yield manager, registry

    # Cleanup all sessions created during E2E tests
    for session in registry.all():
        try:
            await manager.destroy_session(session.id)
        except Exception:
            pass
    await manager.stop()
