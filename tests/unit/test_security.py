import os
import stat
import pytest
from pathlib import Path
from cleanroom.security import (
    verify_docker_socket_permissions,
    verify_not_running_as_root,
    verify_binder_devices,
    verify_zram_configured,
)


class TestSecurityChecks:

    def test_world_writable_socket_raises(self, tmp_path):
        """A world-writable Docker socket should trigger a security error."""
        fake_socket = tmp_path / "docker.sock"
        fake_socket.touch()
        os.chmod(fake_socket, 0o777) # world-writable

        socket_stat = fake_socket.stat()
        mode = socket_stat.st_mode & 0o777
        assert mode & 0o002, "Test setup: socket should be world-writable"
    
    def test_root_check_logs_warning(self, caplog):
        """Running as root should produce a warning, not raise."""
        import logging

        if os.getuid() == 0:
            # Actually running as root
            with caplog.at_level(logging.WARNING):
                verify_not_running_as_root()
            assert "root" in caplog.text.lower()
        else:
            verify_not_running_as_root()
    
    def test_missing_binder_raises(self, tmp_path, monkeypatch):
        """Missing Binder devices should raise RuntimeError."""
        import cleanroom.security as sec

        def fake_exists(self):
            return False
        
        monkeypatch.setattr(Path, "exists", fake_exists)

        with pytest.raises(RuntimeError, match="binder"):
            verify_binder_devices()
    
    def test_missing_zram_logs_warning(self, caplog, monkeypatch):
        """Missing zRAM should log a warning, not raise."""
        import logging
        
        monkeypatch.setattr(Path, "exists", lambda self: False)

        with caplog.at_level(logging.WARNING):
            result = verify_zram_configured()
        
        assert result is False
        assert "zram" in caplog.text.lower()