import logging
from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel

from cleanroom.container.models import Session, SessionStatus

logger = logging.getLogger(__name__)
router = APIRouter()


class CreateSessionResponse(BaseModel):
    session_id: str
    status: SessionStatus
    adb_port: int | None
    expires_at: str | None
    stream_url: str


class SessionStatusResponse(BaseModel):
    session_id: str
    status: SessionStatus
    age_seconds: float
    expires_at: str | None


@router.post(
    "/sessions",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(request: Request) -> CreateSessionResponse:
    f"""
    Spin up a new CleanRoom session.

    Creates a Docker container running Android Go, sets up its isolated
    network, and returns the session details. The session is in BOOTING
    state, which means the caller should poll GET /sessions/{id} until READY.
    """
    manager = request.app.state.container_manager
    registry = request.app.state.registry

    from cleanroom.config import settings
    if registry.count() >= settings.max_sessions:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "capacity_exceeded",
                "message": f"Maximum concurrent sessions ({settings.max_sessions}) reached. Please try again later.",
                "active": registry.count()
            }
        )
    
    try:
        session = await manager.create_session()
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "creation_failed", "message": str(e)}
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "creation_failed", "message": str(e)}
        )
    
    return CreateSessionResponse(
        session_id=session.id,
        status=session.status,
        adb_port=session.adb_port,
        expires_at=session.expires_at.isoformat() if session.expires_at else None,
        stream_url=f"/stream/{session.id}",
    )

@router.get(
    "/sessions/{session_id}",
    response_model=SessionStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def get_session(session_id: str, request: Request) -> SessionStatusResponse:
    """Get the current status of a session."""
    registry = request.app.state.registry
    session = registry.get(session_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": f"Session {session_id} not found"}
        )
    
    return SessionStatusResponse(
        session_id=session.id,
        status=session.status,
        age_seconds=session.age_seconds,
        expires_at=session.expires_at.isoformat() if session.expires_at else None,
    )
    

@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def destroy_session(session_id: str, request: Request) -> None:
    """Destroy a session immediately."""
    registry = request.app.state.registry
    manager = request.app.state.container_manager

    if registry.get(session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": f"Session {session_id} not found"}
        )
    
    try:
        await manager.destroy_session(session_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "destruction_failed", "message": str(e)}
        )
