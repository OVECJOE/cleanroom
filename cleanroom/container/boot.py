import asyncio
import logging

from cleanroom.config import settings
from cleanroom.container.models import Session, SessionStatus
from cleanroom.container.registry import SessionRegistry
from cleanroom.stream.adb import ADBClient

logger = logging.getLogger(__name__)


async def run_boot_pipeline(
    session: Session,
    registry: SessionRegistry,
    destroy_fn,
) -> None:
    """
    Background task: waits for Android to boot, then configure and mark READY

    This runs as an asyncio task, spawned by create_session() immediately after
    the container starts. It completes when Android is read or fails after the
    boot timeout.
    """
    logger.info("Boot pipeline started for session %s", session.id)

    adb = ADBClient(host="127.0.0.1", port=session.adb_port)

    try:
        # Wait for Android to fully boot
        await adb.wait_for_boot(timeout=settings.adb_boot_timeout)

        # Configure proxy (if Tor is enabled)
        if settings.enable_tor and session.tor_container_id:
            from cleanroom.proxy.tor import configure_android_proxy
            try:
                await configure_android_proxy(adb)
            except Exception as e:
                logger.warning(
                    "Proxy configuration failed for session %s: %s",
                    session.id, e
                )
        
        # Mark session as READY
        session.status = SessionStatus.READY
        await registry.update(session)
        logger.info("Session %s is now READY", session.id)
    except TimeoutError:
        logger.error(
            "Session %s boot timed out after %ds",
            session.id, settings.adb_boot_timeout
        )
        session.status = SessionStatus.DEAD
        session.error = "Android boot timed out"
        await registry.update(session)
        try:
            await destroy_fn(session.id)
        except Exception as e:
            logger.error("Failed to destroy timed-out session %s: %s", session.id, e)
    except asyncio.CancelledError:
        logger.info("Boot pipeline for session %s was cancelled", session.id)
    except Exception as e:
        logger.error(
            "Unexpected error in boot pipeline for session %s: %s",
            session.id, e, exc_info=True
        )
        session.status = SessionStatus.DEAD
        session.error = str(e)
        await registry.update(session)
        try:
            await destroy_fn(session.id)
        except Exception:
            pass
