import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from retail_intelligence.shared.bootstrap import initialize
from retail_intelligence.shared.config import CentralSettings
from retail_intelligence.shared.health import check_readiness, database_engine

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = CentralSettings()  # type: ignore[call-arg]
    with database_engine(settings.database_url) as engine:
        initialize(engine, "central")
        app.state.engine = engine
        logger.info("Central started")
        yield


app = FastAPI(title="Central API", lifespan=lifespan)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
def ready(request: Request) -> dict[str, str]:
    return check_readiness(request.app.state.engine)
