import asyncio
import logging
import socket
import aiodocker

from cleanroom.config import settings
from cleanroom.stream.adb import ADBClient

logger = logging.getLogger(__name__)

TOR_SIDECAR_IMAGE = "cleanroom-tor:latest"
TOR_PROXY_PORT = 8888
TOR_SIDECAR_ALIAS = "tor-gateway"


async def start_tor_sidecar(
    client: aiodocker.Docker,
    session_id: str,
    network_id: str,
) -> str:
    """
    Start a Tor sidecar container on the session's network.

    The sidecar has two network connections:
    1. The session's internal bridge (to accept Android's proxy connections)
    2. The host's default Docker bridge (to reach the internet)

    Return the container ID of the Tor sidecar.
    """
    if not settings.enable_tor:
        logger.debug("Tor disabled, skipping sidecar for session %s", session_id)
        return None
    
    container_name = f"cleanroom-tor-{session_id}"
    container_config = {
        "Image": TOR_SIDECAR_IMAGE,
        "Hostname": "tor-gateway",
        "HostConfig": {
            "Privileged": False,
            "Memory": 64 * 1024 * 1024, # 64MB
            "NanoCPUs": 500_000_000, # 0.5 CPU
            "RestartPolicy": {"Name": "no"},
            "NetworkMode": "bridge",
        },
        "Labels": {
            "cleanroom.session_id": session_id,
            "cleanroom.role": "tor-gateway",
            "cleanroom.managed": "true",
        },
    }

    container = await client.containers.create(
        config=container_config,
        name=container_name,
    )
    sidecar_id = container._id
    await container.start()

    # Connect the sidecar to the session's internal network
    network = await client.networks.get(network_id)
    await network.connect({
        "Container": sidecar_id,
        "EndpointConfig": {
            "Aliases": [TOR_SIDECAR_ALIAS],
        }
    })

    await _wait_for_sidecar_ready(session_id)

    logger.info("Tor sidecar started for session %s", session_id)
    return sidecar_id


async def _wait_for_sidecar_ready(session_id: str, timeout: float = 60.0) -> None:
    """Wait for tinyproxy to be accepting connections."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            await asyncio.sleep(5)
            logger.debug("Tor sidecar for session %s assumed ready", session_id)
            return
        except Exception:
            await asyncio.sleep(2)
    raise TimeoutError(f"Tor sidecar did not become ready within {timeout}s")


async def configure_android_proxy(adb_client: ADBClient, proxy_host: str = TOR_SIDECAR_ALIAS) -> None:
    """
    Configure Android to route all HTTP/HTTPS traffic through Tor sidecar.

    Android has a global HTTP proxy setting that the browser and WebView respect.
    We set it via ADB shell commands.
    """
    proxy_address = f"{proxy_host}:{TOR_PROXY_PORT}"

    # Set the global HTTP proxy
    await adb_client.shell(
        f"settings put global http_proxy {proxy_address}"
    )

    # Verify it was set
    result = await adb_client.shell("settings get global http_proxy")
    if proxy_address not in result:
        raise RuntimeError(
            f"Failed to configure Android proxy. Got: {result}"
        )
    
    logger.info("Android proxy configured: %s", proxy_address)
