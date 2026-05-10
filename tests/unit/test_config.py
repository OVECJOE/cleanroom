import pytest
from cleanroom.config import Settings


class TestSettingsDefaults:
    """Settings with no environment variables should have sane defaults."""

    def test_default_max_sessions(self):
        s = Settings()
        assert s.max_sessions == 3
    
    def test_default_memory_mb(self):
        s = Settings()
        assert s.session_memory_mb == 512
    
    def test_memory_bytes_conversion(self):
        s = Settings()
        assert s.session_memory_bytes == 512 * 1024 * 1024
    
    def test_nano_cpus_conversion(self):
        # 1.0 CPU = exactly 1,000,000,000 nano-CPUs
        s = Settings(session_cpus=1.0)
        assert s.adb_nano_cpus == 1_000_000_000
    
    def test_nano_cpus_fractional(self):
        s = Settings(session_cpus=0.5)
        assert s.adb_nano_cpus == 500_000_000


class TestSettingsValidation:
    """Pydantic should reject invalid values at construction time."""

    def test_max_sessions_too_high(self):
        with pytest.raises(Exception):
            Settings(max_sessions=11)
    
    def test_max_sessions_zero(self):
        with pytest.raises(Exception):
            Settings(max_sessions=0)
    
    def test_memory_too_low(self):
        with pytest.raises(Exception):
            Settings(session_memory_mb=100)
    
    def test_port_range_valid(self):
        s = Settings(adb_port_range_start=6000, adb_port_range_end=6100)
        assert s.adb_port_range_start == 6000
        assert s.adb_port_range_end == 6100


class TestSettingsFromEnvironment:
    """Settings should be overridable via environment variables."""

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CLEANROOM_MAX_SESSIONS", "2")
        s = Settings()
        assert s.max_sessions == 2
    
    def test_env_memory_override(self, monkeypatch):
        monkeypatch.setenv("CLEANROOM_SESSION_MEMORY_MB", "768")
        s = Settings()
        assert s.session_memory_mb == 768
        assert s.session_memory_bytes == pytest.approx(768 * 1_024 * 1_024)
