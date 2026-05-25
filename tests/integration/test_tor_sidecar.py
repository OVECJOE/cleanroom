import aiodocker
import pytest

from cleanroom.container.network import create_session_network, destroy_session_network
from cleanroom.proxy.tor import TOR_SIDECAR_ALIAS


@pytest.mark.integration
class TestTorSidecar:
    """
    Integration tests for the Tor sidecar.

    These tests require the cleanroom-tor image to be built locally:
        docker build -t cleanroom-tor docker/tor-sidecar/
    
    If the image is not available, tests are skipped.
    """

    @pytest.fixture(autouse=True)
    async def check_tor_image(self, docker_client: aiodocker.Docker):
        try:
            await docker_client.images.inspect("cleanroom-tor:latest")
        except Exception:
            pytest.skip(
                "cleanroom-tor image not built."
                " Run 'docker build -t cleanroom-tor docker/tor-sidecar/' to build it."
            )
    
    async def test_sidecar_starts_and_runs(self, docker_client: aiodocker.Docker):
        """The Tor sidecar container should start without immediately crashing."""
        session_id = "tor-integ-001"
        network_id = None
        sidecar_id = None

        try:
            network_id = await create_session_network(docker_client, session_id)
            
            from cleanroom.proxy.tor import start_tor_sidecar
            sidecar_id = await start_tor_sidecar(docker_client, session_id, network_id)

            assert sidecar_id is not None

            # Verify container is running
            container = await docker_client.containers.get(sidecar_id)
            info = await container.show()
            assert info["State"]["Running"] is True
        finally:
            if sidecar_id:
                try:
                    c = await docker_client.containers.get(sidecar_id)
                    await c.delete(force=True)
                except Exception:
                    pass
            
            if network_id:
                await destroy_session_network(docker_client, network_id, session_id)

    async def test_sidecar_connected_to_session_network(
        self, docker_client: aiodocker.Docker
    ):
        """Sidecar should be attached to the session bridge and default bridge."""
        session_id = "tor-integ-002"
        network_id = None
        sidecar_id = None

        try:
            network_id = await create_session_network(docker_client, session_id)

            from cleanroom.proxy.tor import start_tor_sidecar
            sidecar_id = await start_tor_sidecar(docker_client, session_id, network_id)

            container = await docker_client.containers.get(sidecar_id)
            info = await container.show()

            # The sidecar should be on at least 2 networks.
            networks = info["NetworkSettings"]["Networks"]
            assert len(networks) >= 2, (
                f"Sidecar should be on 2 networks, found: {list(networks.keys())}"
            )

            # One of those networks should be our session bridge.
            session_network_name = f"cleanroom-net-{session_id}"
            assert any(
                session_network_name in name for name in networks.keys()
            ), f"Session network not found in sidecar networks: {list(networks.keys())}"

        finally:
            if sidecar_id:
                try:
                    c = await docker_client.containers.get(sidecar_id)
                    await c.delete(force=True)
                except Exception:
                    pass
            
            if network_id:
                await destroy_session_network(docker_client, network_id, session_id)
    
    async def test_sidecar_has_correct_alias(self, docker_client: aiodocker.Docker):
        """The sidecar should be reachable as 'tor-gateway' on the session network."""
        session_id = "tor-integ-003"
        network_id = None
        sidecar_id = None

        try:
            network_id = await create_session_network(docker_client, session_id)
            
            from cleanroom.proxy.tor import start_tor_sidecar
            sidecar_id = await start_tor_sidecar(docker_client, session_id, network_id)
            
            container = await docker_client.containers.get(sidecar_id)
            info = await container.show()

            # Find the session network entry
            for net_name, net_config in info["NetworkSettings"]["Networks"].items():
                if "cleanroom-net" in net_name:
                    aliases = net_config.get("Aliases", [])
                    assert TOR_SIDECAR_ALIAS in aliases, (
                        f"'tor-gateway' alias not found. Got aliases: {aliases}"
                    )
                    break
        finally:
            if sidecar_id:
                try:
                    c = await docker_client.containers.get(sidecar_id)
                    await c.delete(force=True)
                except Exception:
                    pass
            
            if network_id:
                await destroy_session_network(docker_client, network_id, session_id)