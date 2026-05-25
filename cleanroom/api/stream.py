import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from cleanroom.container.models import SessionStatus
from cleanroom.stream.adb import ADBClient
from cleanroom.stream.scrcpy import ScrcpyStream

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/stream/{session_id}")
async def stream_session(websocket: WebSocket, session_id: str) -> None:
    f"""
    WebSocket endpoint for the Android screen stream.

    Protocol:
    - Server -> Client: binary messages containing H.264 frames.
      The browser's H.264 decoder can handle these directly.
    - Client -> Server: JSON text messages with the input events.
      Examples:
        - {"type": "tap", "x": 360, "y": 640}
        - {"type": "key", "keycode": 4} (4 = BACK)
        - {"type": "text", "text": "Hello, world!"}
    
    Connection lifecycle:
    1. Client connects.
    2. Server validates session exists and is READY.
    3. Server starts scrcpy stream (or attaches to existing one).
    4. Server sends frames, client sends input events, concurrently.
    5. When client disconnects (or session ends), stream stops.
    """
    registry = websocket.app.state.registry
    session = registry.get(session_id)

    if session is None:
        await websocket.close(code=4004, reason="Session not found")
        return
    
    if session.status != SessionStatus.READY:
        await websocket.close(
            code=4003,
            reason=f"Session not ready (status={session.status})",
        )
        return
    
    await websocket.accept()
    logger.info("WebSocket connected for session %s", session_id)

    adb = ADBClient(host="127.0.0.1", port=session.adb_port)
    stream = ScrcpyStream(adb=adb, session_id=session_id)

    try:
        await stream.start()

        # Run frame sending and input receiving concurrently
        await asyncio.gather(
            _send_frames(websocket, stream, session_id),
            _receive_input(websocket, stream, session_id),
            return_exceptions=False,
        )
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for session %s", session_id)
    except Exception as e:
        logger.error(
            "Stream error for session %s: %s", session_id, e, exc_info=True
        )
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close(code=1011, reason="Stream error")
    finally:
        await stream.stop()
        logger.info("Stream stopped for session %s", session_id)


async def _send_frames(
    websocket: WebSocket,
    stream: ScrcpyStream,
    session_id: str
) -> None:
    """Coroutine: read H.264 frames from scrcpy and send to browser."""
    while True:
        frame = await stream.read_frame()
        if frame is None:
            break
        
        try:
            await websocket.send_bytes(frame)
        except WebSocketDisconnect:
            break
        except Exception as e:
            logger.debug("Frame send error: %s", e)
            break


async def _receive_input(
    websocket: WebSocket,
    stream: ScrcpyStream,
    session_id: str,
) -> None:
    """
    Coroutine: receive input events from browser and forward to Android.

    Input events come as JSON text messages. We parse them and dispatch them
    to the appropriate ADB input command.

    This coroutine blocks on websocket.receive_text() at each iteration.
    When the WebSocket is disconnected, receive_text() raises WebSocketDisconnect,
    which breaks the loop and propagates to asyncio.gather(), which cancels
    the frame-sending coroutine.
    """
    while True:
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            break

        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid input event JSON: %s", raw)
            continue
        
        event_type = event.get("type")
        try:
            if event_type == "tap":
                x, y = int(event["x"]), int(event["y"])
                await stream.send_touch(
                    action=0,
                    x=x,
                    y=y,
                    screen_width=event.get("screen_width", 720),
                    screen_height=event.get("screen_height", 1280),
                )
            elif event_type == "key":
                await stream.adb.send_key(str(event["keycode"]))
            elif event_type == "text":
                await stream.adb.send_text(str(event["text"]))
            elif event_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            else:
                logger.debug("Unknown input event type: %s", event_type)
        except Exception as e:
            logger.error(
                "Error handling input event %s for session %s: %s",
                event_type, session_id, e, exc_info=True
            )
