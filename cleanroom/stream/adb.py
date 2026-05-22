import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# The scrcpy server JAR. This gets pushed into the Android container via ADB
# and runs inside Android to encode the screen as H.264
SCRCPY_SERVER_PATH = Path(__file__).parent / "scrcpy-server.jar"
SCRCPY_SERVER_DEVICE_PATH = "/data/local/tmp/scrcpy-server.jar"
SCRCPY_SERVER_VERSION = "2.4"


class ADBClient:
    """
    Async interface to the ADB command-line tool.

    We shell out to the `adb` binary rather than implementing the ADB
    protocol directly. The protocol is complex and the binary is always
    available on the host where Android development tools are installed.

    Each method creates a subprocess, runs the ADB command, and returns
    the output.
    """

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._serial = f"{host}:{port}"
    
    async def _run(
            self,
            *args: str,
            timeout: float = 10.0,
            check: bool = True,
    ) -> tuple[int, str, str]:
        """
        Run an adb command and return (returncode, stdout, stderr)

        The -s flag tells adb which device to talk to by its serial number
        (host:port for network ADB).
        """
        cmd = ["adb", "-s", self._serial] + list(args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            rc: int = proc.returncode if proc.returncode is not None else 0
            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            stderr_str = stderr.decode("utf-8", errors="replace").strip()

            if check and rc != 0:
                raise RuntimeError(
                    f"ADB command failed (rc={rc}): {' '.join(cmd)}\n"
                    f"stderr: {stderr_str}"
                )
            return rc, stdout_str, stderr_str
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"ADB command timed out after {timeout}s: {' '.join(cmd)}"
            )
    
    async def connect(self) -> bool:
        """
        Connect ADB to the device.

        Returns True if connection succeeded. False otherwise.
        ADB connect can return success codes even when the device is
        not fully ready, so we check the output string explicitly.
        """
        try:
            rc, stdout, _ = await self._run(
                "connect", self._serial, check=False
            )
            success = "connected to" in stdout.lower() or rc == 0
            if success:
                logger.debug("ADB connected to %s", self._serial)
            return success
        except Exception as e:
            logger.debug("ADB connect failed: %s", e)
            return False
    
    async def disconnect(self) -> None:
        try:
            await self._run("disconnect", self._serial, check=False)
        except Exception:
            pass
    
    async def wait_for_boot(self, timeout: float = 90.0) -> None:
        """
        Wait for Android to fully boot.

        The Android system property sys.boot_completed is polled until
        it is set to "1" by Android's init system after all boot services
        have started.
        """
        logger.info("Waiting for Android boot on %s (timeout=%ds)", self._serial, timeout)
        deadline = asyncio.get_event_loop().time() + timeout
        poll_interval = 2.0

        while asyncio.get_event_loop().time() < deadline:
            try:
                # First ensure ADB connection is established
                connected = await self.connect()
                if not connected:
                    await asyncio.sleep(poll_interval)
                    continue

                # Check the boot_completed property
                rc, stdout, _ = await self._run(
                    "shell", "getprop", "sys.boot_completed",
                    timeout=5.0,
                    check=False,
                )
                if rc == 0 and stdout.strip() == "1":
                    logger.info("Android booted on %s", self._serial)
                    return
            except Exception as e:
                logger.debug("Boot poll error: %s", e)
            
            await asyncio.sleep(poll_interval)
        
        raise RuntimeError(
            f"Android did not boot within {timeout}s on {self._serial}"
        )
    
    async def shell(self, command: str, timeout: float = 10.0) -> str:
        """Execute a shell command on the Android device."""
        _, stdout, _ = await self._run("shell", command, timeout=timeout)
        return stdout
    
    async def push(self, local_path: str, remote_path: str) -> None:
        await self._run("push", local_path, remote_path, timeout=30.0)
    
    async def get_prop(self, prop: str) -> str:
        """Get an Android system property."""
        result = await self.shell(f"getprop {prop}")
        return result.strip()
    
    async def send_tap(self, x: int, y: int) -> None:
        """Send a touch tap event at (x, y) in display coordinates."""
        await self.shell(f"input tap {x} {y}")
    
    async def send_key(self, keycode: str) -> None:
        """Send a key press event."""
        await self.shell(f"input keyevent {keycode}")
    
    async def send_text(self, text: str) -> None:
        """Type text into the focused field."""
        # Escape special shell characters
        escaped = text.replace("'", "\\'").replace(" ", "%s")
        await self.shell(f"input text '{escaped}'")
    
    async def is_responsive(self) -> bool:
        try:
            rc, _, _ = await self._run(
                "shell", "echo", "ping",
                timeout=3.0,
                check=False
            )
            return rc == 0
        except Exception:
            return False
