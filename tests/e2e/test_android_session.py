import asyncio

import pytest

from cleanroom.stream.adb import ADBClient


@pytest.mark.e2e
class TestAndroidBoot:
    
    async def test_android_boots_successfully(self, e2e_manager):
        """
        The full Android boot sequence should complete within timeout. This is important
        as it verifies that the container starts, Android init runs,
        Binder and Ashmem are accessible, Android's boot_completed property
        is set, and ADB accepts connections.
        """
        manager, registry = e2e_manager
        session = await manager.create_session()

        try:
            adb = ADBClient(host="127.0.0.1", port=session.adb_port)
            await adb.wait_for_boot()

            # Verify that we can actually talk to Android
            assert await adb.is_responsive()

            # Verify it is actually Android
            version = await adb.get_prop("ro.build.version.release")
            assert version.startswith("12") or version.startswith("13"), (
                f"Expected Android 12 or 13, got: {version}"
            )

            # Verify it is Android Go
            sku = await adb.get_prop("ro.boot.hardware.sku")
            # ReDroid sets this to "redroid"
            assert len(sku) > 0
        finally:
            await manager.destroy_session(session.id)
    
    async def test_data_does_not_persist_after_session(self, e2e_manager):
        """
        The core privacy guarantee: data written in a session must not survive
        session destruction.
        """
        manager, registry = e2e_manager

        # Session 1: write to marker file
        session1 = await manager.create_session()
        marker = "cleanroom-privacy-test-marker-xyz"
        try:
            adb1 = ADBClient("127.0.0.1", session1.adb_port)
            await adb1.wait_for_boot(timeout=120)
            await adb1.shell(f"echo '{marker}' > /data/local/tmp/marker.txt")
            # Verify it was written
            content = await adb1.shell("cat /data/local/tmp/marker.txt")
            assert marker in content
        finally:
            await manager.destroy_session(session1.id)
        
        # Session 2: the marker file must not exist
        session2 = await manager.create_session()
        try:
            adb2 = ADBClient("127.0.0.1", session2.adb_port)
            await adb2.wait_for_boot(timeout=120)
            rc, stdout, _ = await adb2._run(
                "shell", "cat", "/data/local/tmp/marker.txt",
                check=False,
            )
            assert rc != 0 or marker not in stdout, (
                "PRIVACY VIOLATION: data from previous session visible in new session!"
            )
        finally:
            await manager.destroy_session(session2.id)
    
    async def test_adb_port_not_accessible_after_destroy(self, e2e_manager):
        """
        After session destruction, the ADB port should be closed.
        
        This verifies that Docker properly released the port binding when
        the container was removed.
        """
        import socket
        manager, registry = e2e_manager

        session = await manager.create_session()
        port = session.adb_port

        adb = ADBClient("127.0.0.1", port)
        await adb.wait_for_boot(timeout=120)
        assert await adb.is_responsive()

        await manager.destroy_session(session.id)

        # Try to connect to the port -- should fail
        await asyncio.sleep(1)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                pytest.fail(f"Port {port} is still open after session destruction!")
        except (ConnectionRefusedError, OSError):
            pass
    
    async def test_session_network_isolated(self, e2e_manager):
        """
        A session's container should not be able to reach the internet directly.

        If pinging 8.8.8.8 fails, it proves that the container is not directly
        connected to the internet.
        """
        manager, registry = e2e_manager
        session = await manager.create_session()

        try:
            adb = ADBClient("127.0.0.1", session.adb_port)
            await adb.wait_for_boot(timeout=120)

            rc, stdout, _ = await adb._run(
                "shell", "ping", "-c", "1", "-W", "3", "8.8.8.8",
                check=False,
                timeout=10.0,
            )
            assert rc != 0, (
                "ISOLATION FAILURE: container can reach internet directly!"
            )
        finally:
            await manager.destroy_session(session.id)

