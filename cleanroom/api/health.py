from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    active_sessions: int
    max_sessions: int
    docker_connected: bool


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """
    Health check endpoint.

    Used by load balancers and monitoring systems to verify the backend is running.
    """
    registry = request.app.state.registry
    manager = request.app.state.container_manager

    docker_ok = False
    try:
        await manager.client.system.info()
        docker_ok = True
    except Exception:
        pass

    from cleanroom.config import settings
    return HealthResponse(
        status="ok" if docker_ok else "degraded",
        active_sessions=registry.count(),
        max_sessions=settings.max_sessions,
        docker_connected=docker_ok,
    )