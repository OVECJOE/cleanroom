import asyncio
import logging
import aiodocker

logger = logging.getLogger(__name__)


async def create_session_network(client: aiodocker.Docker, session_id: str) -> str:
    """
    Create an isolated Docker bridge network for a session.

    Each session gets its own bridge network. The container on this
    network cannot see containers in any other network.

    Returns the Docker network ID (a 64-char hex string)
    """
    network_name = f"cleanroom-net-{session_id}"
    network = await client.networks.create({
        "Name": network_name,
        "Driver": "bridge",
        "Internal": True, # No external routing. Container is air-gapped
        "Labels": {
            "cleanroom.session_id": session_id,
            "cleanroom.managed": "true",
        }
    })
    network_id = network.id
    logger.info("Created network %s for session %s", network_id, session_id)
    return network_id


async def destroy_session_network(client: aiodocker.Docker, network_id: str, session_id: str) -> None:
    """
    Delete a session's bridge network.

    This also removes all iptables rules Docker created for this network.
    
    Docker will refuse to delete the network if there are still containers attached.
    The caller is responsible for removing containers first.
    """
    try:
        network = await client.networks.get(network_id)
        await network.delete()
        logger.info("Deleted network %s for session %s", network_id, session_id)
    except aiodocker.exceptions.DockerError as e:
        if e.status == 404:
            logger.warning("Network %s not found (already deleted?) for session %s", network_id, session_id)
        else:
            logger.error("Failed to delete network %s: %s", network_id, e)
            raise
