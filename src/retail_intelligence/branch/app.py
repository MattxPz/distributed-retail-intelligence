import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from retail_intelligence.shared.config import BranchSettings
from retail_intelligence.shared.health import check_readiness, database_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = BranchSettings()  # type: ignore[call-arg]
    with database_engine(settings.database_url) as engine:
        app.state.engine = engine
        app.state.branch_id = settings.branch_id
        logger.info("Branch %s started", settings.branch_id)
        yield


app = FastAPI(title="Branch API", lifespan=lifespan)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
def ready(request: Request) -> dict[str, str]:
    result: dict[str, str] = check_readiness(request.app.state.engine)
    result["branch_id"] = str(request.app.state.branch_id)
    return result
