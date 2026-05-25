from cleanroom.stream.adb import ADBClient


class TestADBClientInit:

    def test_serial_format(self):
        client = ADBClient(host="127.0.0.1", port=5555)
        assert client._serial == "127.0.0.1:5555"
    
    def test_custom_host_and_port(self):
        client = ADBClient(host="10.0.0.1", port=5600)
        assert client._serial == "10.0.0.1:5600"
        assert client.host == "10.0.0.1"
        assert client.port == 5600


class TestADBCommandConstruction:
    """Test that our command construction is correct."""

    async def test_connect_constructs_commands(self, monkeypatch):
        """Verify that connect sends 'adb -s serial connect serial'."""
        captured = {}

        async def fake_run(*args, **kwargs):
            captured["cmd"] = args
            return (0, "connected to 127.0.0.1:5555", "")

        client = ADBClient("127.0.0.1", 5555)
        monkeypatch.setattr(client, "_run", fake_run)
        await client.connect()
        assert captured["cmd"][0] == "connect"

    async def test_shell_sends_command(self, monkeypatch):
        captured = {}

        async def fake_run(*args, **kwargs):
            captured["args"] = args
            return (0, "output", "")

        client = ADBClient("127.0.0.1", 5555)
        monkeypatch.setattr(client, "_run", fake_run)
        result = await client.shell("echo hello")
        assert "shell" in captured["args"][0]
        assert result == "output"
