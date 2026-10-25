from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Response, status

from devradar.db import pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    await pool.open()
    yield
    await pool.close()


app = FastAPI(title="DevRadar", lifespan=lifespan)


@app.get("/health")
async def health(response: Response):
    try:
        async with pool.connection(timeout=2) as conn:
            await conn.execute("SELECT 1")
    except psycopg.Error:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "database": "unreachable"}
    return {"status": "ok", "database": "ok"}
