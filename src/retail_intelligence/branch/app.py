import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request
from sqlalchemy import select

from retail_intelligence.branch.models import metadata
from retail_intelligence.branch.sales import SaleInput, create_sale, load_sale
from retail_intelligence.shared.bootstrap import initialize
from retail_intelligence.shared.config import BranchSettings
from retail_intelligence.shared.health import check_readiness, database_engine

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = BranchSettings()  # type: ignore[call-arg]
    with database_engine(settings.database_url) as engine:
        initialize(engine, "branch", settings.branch_id)
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


@app.post("/sales")
def post_sale(
    request: Request,
    data: SaleInput,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
) -> dict[str, Any]:
    return create_sale(request.app.state.engine, request.app.state.branch_id, idempotency_key, data)


@app.get("/products")
def products(request: Request) -> list[dict[str, Any]]:
    with request.app.state.engine.connect() as connection:
        return [
            dict(row) for row in connection.execute(select(metadata.tables["products"])).mappings()
        ]


@app.get("/inventory")
def inventory(request: Request) -> list[dict[str, Any]]:
    table = metadata.tables["inventory"]
    with request.app.state.engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(table).where(table.c.branch_id == request.app.state.branch_id)
            ).mappings()
        ]


@app.get("/sales")
def sales(request: Request) -> list[dict[str, Any]]:
    table = metadata.tables["sales"]
    with request.app.state.engine.connect() as connection:
        ids = connection.execute(
            select(table.c.id).where(table.c.branch_id == request.app.state.branch_id)
        ).scalars()
        return [load_sale(connection, sale_id) for sale_id in ids]


@app.get("/sales/{sale_id}")
def sale(request: Request, sale_id: str) -> dict[str, Any]:
    table = metadata.tables["sales"]
    with request.app.state.engine.connect() as connection:
        if (
            connection.scalar(
                select(table.c.id).where(
                    table.c.id == sale_id, table.c.branch_id == request.app.state.branch_id
                )
            )
            is None
        ):
            raise HTTPException(404, "Sale not found")
        return load_sale(connection, sale_id)
