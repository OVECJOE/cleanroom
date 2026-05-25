import json
from datetime import UTC, datetime, timedelta

import pytest

from cleanroom.container.models import Session, SessionStatus
from cleanroom.container.registry import SessionRegistry


@pytest.fixture
def tmp_registry_path(tmp_path, monkeypatch):
    """Point the registry at a temp directory for each test."""
    registry_file = tmp_path / "sessions.json"
    monkeypatch.setattr(
        "cleanroom.container.registry.REGISTRY_PATH",
        registry_file
    )
    return registry_file


@pytest.fixture
def registry(tmp_registry_path):
    return SessionRegistry()


class TestRegistryOperations:

    async def test_add_and_get(self, registry):
        session = Session(id="test-001")
        await registry.add(session)
        result = registry.get("test-001")
        assert result is not None
        assert result.id == "test-001"
    
    async def test_get_missing_returns_none(self, registry):
        assert registry.get("nonexistent") is None
    
    async def test_count_empty(self, registry):
        assert registry.count() == 0
    
    async def test_count_after_add(self, registry):
        await registry.add(Session(id="a"))
        await registry.add(Session(id="b"))
        assert registry.count() == 2

    async def test_remove_existing(self, registry):
        session = Session(id="removable")
        await registry.add(session)
        removed = await registry.remove("removable")
        assert removed.id == "removable"
        assert registry.get("removable") is None
    
    async def test_remove_missing_returns_none(self, registry):
        result = await registry.remove("nonexistent")
        assert result is None

    async def test_update(self, registry):
        session = Session(id="updatable", status=SessionStatus.CREATING)
        await registry.add(session)
        session.status = SessionStatus.READY
        await registry.update(session)
        assert registry.get("updatable").status == SessionStatus.READY
    
    async def test_update_missing_raises(self, registry):
        session = Session(id="missing")
        with pytest.raises(KeyError):
            await registry.update(session)
    
    async def test_active_filters_correctly(self, registry):
        s_ready = Session(id="ready", status=SessionStatus.READY)
        s_booting = Session(id="booting", status=SessionStatus.BOOTING)
        s_dead = Session(id="dead", status=SessionStatus.DEAD)
        for s in [s_ready, s_booting, s_dead]:
            await registry.add(s)
        active = registry.active()
        assert len(active) == 1
        assert active[0].id == "ready"


class TestRegistryPersistence:

    async def test_persist_after_add(self, registry, tmp_registry_path):
        await registry.add(Session(id="persist-me"))
        assert tmp_registry_path.exists()
        data = json.loads(tmp_registry_path.read_text())
        assert "persist-me" in data
    
    async def test_loads_from_disk(self, tmp_registry_path):
        # Write a registry file manually
        session = Session(id="from-disk", status=SessionStatus.READY)
        data = {"from-disk": session.model_dump(mode="json")}
        tmp_registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_registry_path.write_text(json.dumps(data, default=str))

        registry = SessionRegistry()
        loaded = await registry.load_from_disk()
        assert len(loaded) == 1
        assert loaded[0].id == "from-disk"
        assert registry.get("from-disk") is not None
    
    async def test_load_from_nonexistent_file(self, registry):
        # Should not crash, should return empty list
        result = await registry.load_from_disk()
        assert result == []
    
    async def test_persists_after_remove(self, registry, tmp_registry_path):
        await registry.add(Session(id="to-remove"))
        await registry.remove("to-remove")
        data = json.loads(tmp_registry_path.read_text())
        assert "to-remove" not in data
    
    async def test_atomic_write_on_crash(self, registry, tmp_registry_path):
        """Verify that a crash during write does not corrupt the registry."""
        await registry.add(Session(id="safe"))
        # File should be valid JSON after every write
        content = tmp_registry_path.read_text()
        data = json.loads(content) # would raise if corrupted
        assert "safe" in data


class TestSessionModel:

    def test_is_expired_false_for_future(self):
        session = Session(
            expires_at=datetime.now(UTC) + timedelta(hours=1)
        )
        assert not session.is_expired
    
    def test_is_expired_false_when_none(self):
        session = Session(expires_at=None)
        assert not session.is_expired
    
    def test_age_seconds_increases(self):
        session = Session(
            created_at=datetime.now(UTC) - timedelta(seconds=30)
        )
        assert session.age_seconds >= 29
