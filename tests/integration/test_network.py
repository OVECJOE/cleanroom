import aiodocker
import pytest

from cleanroom.container.network import create_session_network, destroy_session_network


@pytest.mark.integration
class TestNetworkCreation:

    async def test_creates_network(self, docker_client: aiodocker.Docker):
        """
        A  real Docker bridge network should be created and findable.

        This test verifies that:
        1. Our create_session_network call succeeds.
        2. The network exists in Docker after creation.
        3. The network has the labels we set.
        """
        session_id = "integ-test-net-001"
        network_id = None

        try:
            network_id = await create_session_network(docker_client, session_id)
            assert network_id is not None
            assert len(network_id) > 0

            # Verify Docker knows about this network
            network = await docker_client.networks.get(network_id)
            info = await network.show()

            assert info["Name"] == f"cleanroom-net-{session_id}"
            assert info["Labels"]["cleanroom.session_id"] == session_id
            assert info["Labels"]["cleanroom.managed"] == "true"
            # Internal=True means no external routing.
            assert info["Internal"] is True
        finally:
            if network_id:
                await destroy_session_network(docker_client, network_id, session_id)
    
    async def test_destroys_network(self, docker_client: aiodocker.Docker):
        """After destroy, the network should not exist in Docker."""
        session_id = "integ-test-net-002"
        network_id = await create_session_network(docker_client, session_id)

        await destroy_session_network(docker_client, network_id, session_id)

        # Verify it is gone
        with pytest.raises(aiodocker.exceptions.DockerError) as exc_info:
            await docker_client.networks.get(network_id)
        assert exc_info.value.status == 404
    
    async def test_destroy_nonexistent_does_not_crash(
        self, docker_client: aiodocker.Docker
    ):
        """Destroying a non-existent network should be handled gracefully."""
        fake_id = "a" * 64 # plausible-looking Docker ID
        await destroy_session_network(docker_client, fake_id, "ghost-session")

    async def test_creates_unique_networks(self, docker_client: aiodocker.Docker):
        """Each session should get a network with a unique name."""
        ids = []
        try:
            for i in range(3):
                nid = await create_session_network(
                    docker_client, f"unique-session-{i:04d}"
                )
                ids.append((f"unique-session-{i:04d}", nid))
            # All network IDs should be unique
            assert len(set(nid for _, nid in ids)) == 3
        finally:
            for sid, nid in ids:
                try:
                    await destroy_session_network(docker_client, nid, sid)
                except Exception:
                    pass

