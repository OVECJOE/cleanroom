from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from cleanroom.container.models import Session, SessionStatus
from cleanroom.container.registry import SessionRegistry
from cleanroom.watchdog.ttl import TTLWatchdog


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cleanroom.container.registry.REGISTRY_PATH", tmp_path / "s.json"
    )
    return SessionRegistry()


@pytest.fixture
def destroy_mock():
    return AsyncMock()


@pytest.fixture
def watchdog(registry, destroy_mock):
    return TTLWatchdog(registry, destroy_mock, check_interval=0.1)


class TestTTLEnforcement:

    async def test_destroys_expired_session(self, watchdog, registry, destroy_mock):
        session = Session(
            id="expired",
            status=SessionStatus.READY,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        await registry.add(session)
        await watchdog._check_sessions()
        destroy_mock.assert_awaited_once_with("expired")

    async def test_skips_dead_sessions(self, watchdog, registry, destroy_mock):
        session = Session(
            id="dead",
            status=SessionStatus.DEAD,
            expires_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        await registry.add(session)
        await watchdog._check_sessions()
        destroy_mock.assert_not_called()
    
    async def test_skips_destroying_sessions(self, watchdog, registry, destroy_mock):
        session = Session(
            id="destroying",
            status=SessionStatus.DESTROYING,
            expires_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        await registry.add(session)
        await watchdog._check_sessions()
        destroy_mock.assert_not_called()
    
    async def test_continues_after_destroy_error(self, watchdog, registry):
        """Watchdog must not crash if one session's destruction fails."""
        error_mock = AsyncMock(side_effect=RuntimeError("docker down"))
        watchdog._destroy = error_mock

        session1 = Session(
            id="fail",
            status=SessionStatus.READY,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        session2 = Session(
            id="also-expired",
            status=SessionStatus.READY,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        await registry.add(session1)
        await registry.add(session2)
        await watchdog._check_sessions()
        assert error_mock.call_count == 2
    
    async def test_start_and_stop(self, watchdog):
        await watchdog.start()
        assert watchdog._task is not None
        assert not watchdog._task.done()
        await watchdog.stop()
        assert watchdog._task.done()
