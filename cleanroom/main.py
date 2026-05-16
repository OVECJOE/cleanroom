import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cleanroom.config import settings
from cleanroom.container.manager import ContainerManager
from cleanroom.container.registry import SessionRegistry
from cleanroom.watchdog.ttl import TTLWatchdog

# Configure logging before anything else
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)

logger = logging.getLogger(__name__)


def create_application() -> FastAPI:
    """Application factory"""
    registry = SessionRegistry()
    container_manager = ContainerManager(registry)
    watchdog = TTLWatchdog(
        registry=registry,
        destroy_fn=container_manager.destroy_session,
        check_interval=30.0,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """
        Application lifespan: startup and shutdown logic.
        
        This is where we connect to Docker, start the watchdog,
        perform crash recovery, and register the shared objects so
        route handlers can access them via app.state.
        """
        logger.info("CleanRoom starting up...")

        # Make shared objects accessible to route handlers
        app.state.registry = registry
        app.state.container_manager = container_manager
        app.state.watchdog = watchdog

        # Create the registry directory if it doesn't exist yet
        from cleanroom.container.registry import REGISTRY_PATH
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Connect to Docker
        await container_manager.start()

        # Perform crash recovery before starting the watchdog.
        await watchdog.recover_from_crash(container_manager)

        # Start the TTL watchdog background task
        await watchdog.start()

        logger.info("CleanRoom ready and listening on %s", app.url_path_for("root"))
        yield

        # Shutdown: stop the watchdog, then the Docker client.
        logger.info("CleanRoom shutting down...")
        await watchdog.stop()
        await container_manager.stop()
        logger.info("CleanRoom shutdown complete")
    
    app = FastAPI(
        title="CleanRoom API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    # Register routes
    from cleanroom.api.sessions import router as sessions_router
    from cleanroom.api.health import router as health_router

    app.include_router(sessions_router, prefix="/api")
    app.include_router(health_router)

    return app


app = create_application()
