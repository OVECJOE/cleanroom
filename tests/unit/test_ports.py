import asyncio
import socket

import pytest

from cleanroom.config import Settings
from cleanroom.container.ports import PortPool


@pytest.fixture
def small_pool(monkeypatch):
    """A pool with a small, controlled range for testing."""
    monkeypatch.setattr(
        "cleanroom.container.ports.settings",
        Settings(adb_port_range_start=19000, adb_port_range_end=19009)
    )
    return PortPool()


class TestPortPool:

    async def test_acquire_returns_port_in_range(self, small_pool):
        port = await small_pool.acquire()
        assert 19000 <= port <= 19009
    
    async def test_acquire_different_ports(self, small_pool):
        port1 = await small_pool.acquire()
        port2 = await small_pool.acquire()
        assert port1 != port2
    
    async def test_release_makes_port_available_again(self, small_pool):
        port = await small_pool.acquire()
        in_use_before = small_pool.in_use_count
        await small_pool.release(port)
        assert small_pool.in_use_count == in_use_before - 1
        assert small_pool.available_count > 0
    
    async def test_exhaust_pool_raises(self, small_pool):
        """Acquiring more ports than available should raise."""
        ports = []
        for _ in range(10):
            ports.append(await small_pool.acquire())
        with pytest.raises(RuntimeError, match="No available ADB ports"):
            await small_pool.acquire()
    
    async def test_concurrent_acquire_no_duplicates(self, small_pool):
        """
        Ten concurrent acquires should all get unique ports.

        This tests the asyncio.Lock is working correctly. Without the lock,
        concurrent coroutines could claim the same port.
        """
        ports = await asyncio.gather(*[small_pool.acquire() for _ in range(5)])
        assert len(set(ports)) == 5  # All ports should be unique
    
    async def test_skips_bound_ports(self, small_pool):
        """
        If a port in our range is already bound by something else,
        the pool should skip it and return the next one.
        """
        # Bind port 19000 to simulate an external process holding it
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 19000))
            port = await small_pool.acquire()
            # Should have skipped 19000 and taken the next one
            assert port != 19000
    
    async def test_reclaim(self, small_pool):
        """Reclaim should mark ports as in-use without going through acquire."""
        initial_available = small_pool.available_count
        await small_pool.reclaim([19001, 19002])
        assert small_pool.available_count == initial_available - 2
        assert small_pool.in_use_count == 2
