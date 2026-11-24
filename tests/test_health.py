import httpx
import psycopg
import pytest

from devradar.api.main import create_app
from devradar.db import create_pool

pytestmark = pytest.mark.anyio


async def get_health(conninfo):
    app = create_app()
    async with create_pool(conninfo) as pool:
        app.state.pool = pool
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get("/health")


def add_run(conninfo, status, age):
    with psycopg.connect(conninfo) as conn:
        conn.execute(
            """
            INSERT INTO ingestion_runs (trigger, status, finished_at)
            VALUES ('schedule', %s, now() - %s::interval)
            """,
            (status, age),
        )


async def test_recent_ingestion_is_healthy(migrated_db):
    add_run(migrated_db, "partial", "1 hour")
    response = await get_health(migrated_db)
    assert (response.status_code, response.json()["status"]) == (200, "ok")


@pytest.mark.parametrize(
    ("status", "age"), [("succeeded", "9 days"), ("failed", "1 minute")]
)
async def test_old_or_failed_ingestion_is_reported_as_stale(migrated_db, status, age):
    add_run(migrated_db, status, age)
    response = await get_health(migrated_db)
    assert (response.status_code, response.json()["status"]) == (200, "stale")


async def test_never_ingested_is_stale(migrated_db):
    body = (await get_health(migrated_db)).json()
    assert (body["status"], body["last_ingestion_at"]) == ("stale", None)
