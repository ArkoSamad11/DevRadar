import psycopg
import pytest

from devradar.db import create_pool


@pytest.mark.anyio
async def test_api_queries_have_a_time_limit(migrated_db):
    async with create_pool(migrated_db) as pool, pool.connection() as conn:
        assert await (await conn.execute("SHOW statement_timeout")).fetchone() == (
            "5s",
        )
        await conn.execute("SET statement_timeout = 10")
        with pytest.raises(psycopg.errors.QueryCanceled):
            await conn.execute("SELECT pg_sleep(1)")
