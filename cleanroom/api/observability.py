"""
Observability: structured metrics and diagnostics

TODO: replace with Prometheus metrics and connect to Grafana 
"""
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from cleanroom.container.manager import ContainerManager
from cleanroom.container.models import SessionStatus
from cleanroom.container.registry import SessionRegistry

logger = logging.getLogger(__name__)
router = APIRouter()


class SessionMetrics(BaseModel):
    session_id: str
    status: str
    age_seconds: float
    memory_used_bytes: int | None
    memory_limit_bytes: int
    cpu_throttle_percent: float | None


class SystemMetrics(BaseModel):
    timestamp: float
    host_memory_total_mb: int
    host_memory_available_mb: int
    host_memory_used_percent: float
    zram_compressed_mb: int | None
    zram_compressed_mb: int | None
    active_sessions: int
    max_sessions: int
    sessions: list[SessionMetrics]


@router.get("/metrics", response_model=SystemMetrics)
async def metrics(request: Request) -> SystemMetrics:
    """
    System and session metrics.

    Host memory: read from /proc/meminfo (updates in real-time)
    Container memory: read from cgroup memory stats via Docker API
    zRAM: read from /sys/block/zram0/mm_stat
    """
    registry: SessionRegistry = request.app.state.registry
    manager: ContainerManager = request.app.state.container_manager

    # Read host memory from /proc/meminfo
    mem_total, mem_available = _read_proc_meminfo()

    # Read zRAM stats
    zram_compressed = _read_zram_stats()

    # Read per-session container stats
    session_metrics = []
    from cleanroom.config import settings

    for session in registry.all():
        container_memory = None
        cpu_throttle = None

        if session.container_id and session.status == SessionStatus.READY:
            try:
                stats = await manager.get_container_stats(session.container_id)
                mem_stats = stats.get("memory_stats", {})
                container_memory = mem_stats.get("usage", None)

                cpu_stats = stats.get("cpu_stats", {})
                throttle = cpu_stats.get("throttling_data", {})
                throttled = throttle.get("throttled_periods", 0)
                total = throttle.get("total_periods", 0)
                if total > 0:
                    cpu_throttle = (throttled / total) * 100
            except Exception:
                pass
        
        session_metrics.append(SessionMetrics(
            session_id=session.id,
            status=session.status,
            age_seconds=session.age_seconds,
            memory_used_bytes=container_memory,
            memory_limit_bytes=settings.session_memory_bytes,
            cpu_throttle_percent=cpu_throttle,
        ))
    
    used_percent = 0.0
    if mem_total > 0:
        used_percent = ((mem_total - mem_available) / mem_total) * 100
    
    return SystemMetrics(
        timestamp=time.time(),
        host_memory_total_mb=mem_total // 1024,
        host_memory_available_mb=mem_available // 1024,
        host_memory_used_percent=round(used_percent, 1),
        zram_compressed_mb=zram_compressed,
        active_sessions=registry.count(),
        max_sessions=settings.max_sessions,
        sessions=session_metrics
    )


def _read_proc_meminfo() -> tuple[int, int]:
    """
    Read MemTotal and MemAvailable from /proc/meminfo.
    
    Returns (total_kb, available_kb)
    """
    try:
        meminfo = Path("/proc/meminfo").read_text()
        total = available = 0
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                available = int(line.split()[1])
        return total, available
    except Exception:
        return 0, 0


def _read_zram_stats() -> int | None:
    """
    Read zRAM compressed data size from /sys/block/zram0/mm_stat.

    The mm_stat file has the format:
    orig_data_size compr_data_size mem_used_total ...

    Returns compressed size in MB.
    """
    try:
        mm_stat = Path("/sys/block/zram0/mm_stat").read_text().split()
        compr_bytes = int(mm_stat[1]) # compr_data_size
        return compr_bytes // (1024 * 1024)
    except Exception:
        return None