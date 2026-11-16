from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request, Response, status

from devradar.api import jobs
from devradar.db import create_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with create_pool() as pool:
        app.state.pool = pool
        yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="DevRadar",
        description="Search tech internship postings collected from company job boards.",
        lifespan=lifespan,
    )
    app.include_router(jobs.router)
    app.add_api_route("/health", health, methods=["GET"], tags=["meta"])
    return app


async def health(request: Request, response: Response):
    try:
        async with request.app.state.pool.connection(timeout=2) as conn:
            await conn.execute("SELECT 1")
    except psycopg.Error:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "database": "unreachable"}
    return {"status": "ok", "database": "ok"}


app = create_app()
