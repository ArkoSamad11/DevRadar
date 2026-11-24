"""Compare ingestion write cost between two copies of the database. All writes are rolled back.

usage: uv run python scripts/benchmark_writes.py BEFORE_DB AFTER_DB
"""

import asyncio
import statistics
import sys
import time

import psycopg
from psycopg.conninfo import make_conninfo

from devradar.config import settings
from devradar.ingestion.models import Board, Posting
from devradar.ingestion.store import store_postings

POSTINGS = 1000
ROUNDS = 7


class RollBack(Exception):
    pass


def make_postings(descriptions: list[str], version: str) -> list[Posting]:
    return [
        Posting(
            source="ashby",
            external_id=f"bench-{i}",
            title=f"Software Engineer {'Intern' if i % 20 == 0 else 'II'} {version}",
            url="https://example.test",
            locations=("New York, NY",),
            posted_at=None,
            employment_type=None,
            raw={"id": i, "version": version},
            description=descriptions[i % len(descriptions)] if i % 20 == 0 else None,
        )
        for i in range(POSTINGS)
    ]


async def timed_store(conn, board: Board, postings: list[Posting]) -> tuple[float, int]:
    lsn = "SELECT pg_current_wal_insert_lsn() - '0/0'::pg_lsn"
    (before,) = await (await conn.execute(lsn)).fetchone()
    started = time.perf_counter()
    await store_postings(conn, board, postings)
    elapsed = (time.perf_counter() - started) * 1000
    (after,) = await (await conn.execute(lsn)).fetchone()
    return elapsed, int(after - before)


async def one_round(conninfo: str) -> dict[str, tuple[float, int]]:
    result = {}
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        rows = await conn.execute(
            "SELECT description FROM jobs WHERE description IS NOT NULL ORDER BY id LIMIT 50"
        )
        descriptions = [row[0] for row in await rows.fetchall()]
        cursor = await conn.execute(
            "SELECT id, company_id FROM job_boards WHERE ats = 'ashby' ORDER BY id LIMIT 1"
        )
        board_id, company_id = await cursor.fetchone()
        board = Board(board_id, company_id, "ashby", "bench")
        try:
            async with conn.transaction():
                for name, version in (
                    ("insert", "v1"),
                    ("unchanged", "v1"),
                    ("update", "v2"),
                ):
                    result[name] = await timed_store(
                        conn, board, make_postings(descriptions, version)
                    )
                raise RollBack
        except RollBack:
            pass
        await conn.execute(
            "VACUUM ANALYZE jobs, job_sources, job_locations, job_categories"
        )
    return result


def main() -> None:
    databases = sys.argv[1:3]
    samples: dict[tuple[str, str], list[tuple[float, int]]] = {}
    for round_number in range(ROUNDS + 1):
        order = databases if round_number % 2 else databases[::-1]
        for name in order:
            result = asyncio.run(
                one_round(make_conninfo(settings.database_url, dbname=name))
            )
            if round_number:
                for step, sample in result.items():
                    samples.setdefault((step, name), []).append(sample)
    print(f"{POSTINGS} postings, median of {ROUNDS} rounds")
    for step in ("insert", "unchanged", "update"):
        cells = []
        for name in databases:
            times, wal = zip(*samples[step, name], strict=True)
            cells.append(
                f"{statistics.median(times):7.0f} ms (min {min(times):.0f}, max {max(times):.0f}) "
                f"{statistics.median(wal) / 1e6:5.2f} MB WAL"
            )
        print(f"{step:10} " + "  ->  ".join(cells))


if __name__ == "__main__":
    main()
