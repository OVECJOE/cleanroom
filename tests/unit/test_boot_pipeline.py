import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from cleanroom.container.models import Session, SessionStatus
from cleanroom.container.registry import SessionRegistry
from cleanroom.container.boot import run_boot_pipeline


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cleanroom.container.registry.REGISTRY_PATH",
        tmp_path / "s.json"
    )
    return SessionRegistry()


@pytest.fixture
def session(registry):
    return Session(
        id="boot-test",
        adb_port=5555,
        container_id="abc123",
        status=SessionStatus.BOOTING,
    )


@pytest.fixture
def destroy_mock():
    return AsyncMock()


class TestBootPipeline:

    async def test_marks_ready_on_successful_boot(self, session, registry, destroy_mock):
        """A successful boot should set session status to READY."""
        await registry.add(session)

        with patch("cleanroom.container.boot.ADBClient") as MockADB:
            mock_adb = MockADB.return_value
            mock_adb.wait_for_boot = AsyncMock()

            # Disable Tor for this test
            with patch("cleanroom.container.boot.settings") as mock_settings:
                mock_settings.enable_tor = False
                mock_settings.adb_boot_timeout = 5

                await run_boot_pipeline(session, registry, destroy_mock)
        
        result = registry.get("boot-test")
        assert result.status == SessionStatus.READY
        destroy_mock.assert_not_called()
    
    async def test_destroys_session_on_timeout(self, session, registry, destroy_mock):
        """A boot timeout should mark the session DEAD and trigger destroy."""
        await registry.add(session)

        with patch("cleanroom.container.boot.ADBClient") as MockADB:
            mock_adb = MockADB.return_value
            mock_adb.wait_for_boot = AsyncMock(side_effect=TimeoutError("Boot timeout"))

            with patch("cleanroom.container.boot.settings") as mock_settings:
                mock_settings.enable_tor = False
                mock_settings.adb_boot_timeout = 5

                await run_boot_pipeline(session, registry, destroy_mock)
        
        result = registry.get("boot-test")
        assert result.status == SessionStatus.DEAD
        destroy_mock.assert_awaited_once_with("boot-test")
    
    async def test_continues_if_proxy_config_fails(self, session, registry, destroy_mock):
        """A proxy configuration failure should not abort the session."""
        await registry.add(session)
        session.tor_container_id = "some-tor-container"

        with patch("cleanroom.container.boot.ADBClient") as MockADB:
            mock_adb = MockADB.return_value
            mock_adb.wait_for_boot = AsyncMock()

            with patch("cleanroom.container.boot.settings") as mock_settings:
                mock_settings.enable_tor = True
                mock_settings.adb_boot_timeout = 5

                with patch(
                    "cleanroom.proxy.tor.configure_android_proxy",
                    AsyncMock(side_effect=RuntimeError("proxy failed"))
                ):
                    await run_boot_pipeline(session, registry, destroy_mock)
        
        result = registry.get("boot-test")
        assert result.status == SessionStatus.READY
        destroy_mock.assert_not_called()
    
    async def test_handles_cancellation_cleanly(self, session, registry, destroy_mock):
        """
        If the boot task is cancelled (session destroyed during boot),
        it should exit cleanly instead of raising.
        """
        await registry.add(session)

        async def slow_boot(*args, **kwargs):
            await asyncio.sleep(60)  # simulate long boot

        with patch("cleanroom.container.boot.ADBClient") as MockADB:
            mock_adb = MockADB.return_value
            mock_adb.wait_for_boot = slow_boot

            with patch("cleanroom.container.boot.settings") as mock_settings:
                mock_settings.enable_tor = False
                mock_settings.adb_boot_timeout = 60

                task = asyncio.create_task(
                    run_boot_pipeline(session, registry, destroy_mock)
                )
                await asyncio.sleep(0.1)  # Let the task start
                task.cancel()

                await task  # Should complete without raising

        result = registry.get("boot-test")
        assert result.status == SessionStatus.BOOTING  # Never reached READY
