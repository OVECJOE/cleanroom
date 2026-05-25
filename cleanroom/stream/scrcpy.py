import asyncio
import logging
import struct
from pathlib import Path

from cleanroom.stream.adb import ADBClient

logger = logging.getLogger(__name__)

# The scrcpy server jar version bundled with CleanRoom.
SCRCPY_VERSION = "2.4"
# Path to the bundled scrcpy server jar (inside the cleanroom package)
SCRCPY_SERVER_JAR = Path(__file__).parent / "assets" / "scrcpy-server.jar"
# Path inside Android where we push the server
DEVICE_SERVER_PATH = "/data/local/tmp/scrcpy-server.jar"
# The port on the host side of ADB forwarding
SCRCPY_LOCAL_PORT = 27183
# H.264 unit start code
H264_NAL_START = b'\x00\x00\x00\x01'


class ScrcpyStream:
    """
    Manages the scrcpy server lifecycle and video stream.

    scrcpy works in two parts:
    1. scrcpy-server.jar: runs inside Android, captures the screen,
        encodes as H.264, writes frames to a socket.
    2. scrcpy client: reads from the socket and renders the video.

    This is a minimal implementation of part 2 where we read frames,
    and forward them to the browser via WebSocket.

    Frame format (scrcpy wire protocol):
    - Each video frame is prefixed with an 8-byte header:
        - 4 bytes: PTS (presentation timestamp) in microseconds.
        - 4 bytes: frame data length in bytes.
    - Then the raw H.264 NAL units for that frame.
    """

    def __init__(self, adb: ADBClient, session_id: str):
        self.adb = adb
        self.session_id = session_id
        self._server_process: asyncio.subprocess.Process | None = None
        self._local_port: int = SCRCPY_LOCAL_PORT
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
    
    async def start(self, width: int = 720, height: int = 1280) -> None:
        """Push the scrcpy server to Android, start it, and establish the stream."""
        # Push the server JAR to Android's writable data partition.
        if SCRCPY_SERVER_JAR.exists():
            await self.adb.push(str(SCRCPY_SERVER_JAR), DEVICE_SERVER_PATH)
        else:
            raise FileNotFoundError(
                f"scrcpy server jar not found at {SCRCPY_SERVER_JAR}. "
                f"Download from https://github.com/Genymobile/scrcpy/releases"
            )
        
        # Set up ADB port forwarding
        await self.adb._run(
            "forward",
            f"tcp:{self._local_port}",
            "localabstract:scrcpy", # Android's abstract Unix socket namespace
        )

        # Start the scrcpy server inside Android
        server_cmd = (
            f"CLASSPATH={DEVICE_SERVER_PATH} "
            f"app_process / com.genymobile.scrcpy.Server "
            f"{SCRCPY_VERSION} "
            f"tunnel_forward=true "
            f"video_bit_rate=2000000 "
            f"max_size={max(width, height)} "
            f"lock_video_orientation=0 "
            f"control=true "
            f"display_id=0 "
            f"show_touches=false "
        )
        self._server_process = await asyncio.create_subprocess_exec(
            "adb", "-s", self.adb._serial, "shell", server_cmd + " &>/dev/null &",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # Wait for the server to be ready and connect
        await asyncio.sleep(1)
        await self._connect_to_server()

        logger.info("scrcpy stream started for session %s", self.session_id)
    
    async def _connect_to_server(self, retries: int = 5) -> None:
        """Connect to the scrcpy server via the ADB-forwarded port."""
        last_error = None
        for attempt in range(retries):
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    "127.0.0.1", self._local_port,
                )
                # Read the device info header (68 bytes)
                # Format: 64-byte device name + 2-byte width + 2-byte height
                header = await asyncio.wait_for(
                    self._reader.readexactly(68), timeout=5.0
                )
                device_name = (
                    header[:64].rstrip(b'\x00').decode("utf-8", errors="replace")
                )
                width = struct.unpack(">H", header[64:66])[0]
                height = struct.unpack(">H", header[66:68])[0]
                logger.info(
                    "scrcpy connected to %s (%dx%d)",
                    device_name, width, height
                )
                return
            except Exception as e:
                last_error = e
                logger.debug(
                    "scrcpy connection attempt %d failed: %s",
                    attempt + 1, e
                )
                await asyncio.sleep(1)
            
        raise RuntimeError(
            f"Could not connect to scrcpy server after {retries} attempts: {last_error}"
        )
    
    async def read_frame(self) -> bytes | None:
        """
        Read the next H.264 frame from the scrcpy stream.

        Frame wire format:
        - 8 bytes header: 4-byte PTS (big-endian) + 4-byte frame size (big-endian)
        - N bytes: H.264 NAL units

        Returns the raw H.264 bytes (without the scrcpy header), ready to send to the
        browser's H.264 decoder.
        Returns None if the stream is closed.
        """
        if not self._reader:
            return None
        
        try:
            # Read 12-byte frame header
            header = await asyncio.wait_for(
                self._reader.readexactly(12), timeout=5.0
            )
            size = struct.unpack(">I", header[8:12])[0]

            if size == 0:
                return None
            
            frame_data = await asyncio.wait_for(
                self._reader.readexactly(size), timeout=10.0
            )
            return frame_data
        except asyncio.IncompleteReadError:
            logger.info("scrcpy stream ended for session %s", self.session_id)
            return None
        except TimeoutError:
            logger.warning("scrcpy frame read timeout for session %s", self.session_id)
            return None
    
    async def send_touch(
        self,
        action: int,
        x: int,
        y: int,
        screen_width: int,
        screen_height: int,
    ) -> None:
        """
        Send a touch event to Android via the control socket.

        The scrcpy control protocol is a separate connection on the
        ADB forward tunnel, on a different port.

        For now, a simpler approach of ADB shell input commands is used,
        which has slightly higher latency but no additional protocol implementation.
        """
        await self.adb.send_tap(x, y)
    
    async def stop(self) -> None:
        """Stop the scrcpy server and clean up the ADB forward."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        
        # Remove the ADB port forward
        try:
            await self.adb._run(
                "forward", "--remove", f"tcp:{self._local_port}",
                check=False,
            )
        except Exception:
            pass
        
        # Kill the server process inside Android
        try:
            await self.adb.shell(
                "pkill -f 'com.genymobile.scrcpy.Server' || true"
            )
        except Exception:
            pass
        
        logger.info("scrcpy stream stopped for session %s", self.session_id)
