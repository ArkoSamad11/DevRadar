from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request, Response, status

from devradar.api import companies, jobs, pipeline
from devradar.config import settings
from devradar.db import create_pool

MISSED_RUNS_BEFORE_STALE = 3


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
    app.include_router(companies.router)
    app.include_router(pipeline.router)
    app.add_api_route("/health", health, methods=["GET"], tags=["meta"])
    return app


async def health(request: Request, response: Response):
    try:
        async with request.app.state.pool.connection(timeout=2) as conn:
            cursor = await conn.execute(
                """
                SELECT max(finished_at),
                       coalesce(max(finished_at) > now() - make_interval(mins => %s), false)
                FROM ingestion_runs WHERE status IN ('succeeded', 'partial')
                """,
                (int(settings.ingest_interval_minutes * MISSED_RUNS_BEFORE_STALE),),
            )
            last_ingestion_at, fresh = await cursor.fetchone()
    except psycopg.Error:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "database": "unreachable"}
    return {
        "status": "ok" if fresh else "stale",
        "database": "ok",
        "last_ingestion_at": last_ingestion_at,
    }


app = create_app()
