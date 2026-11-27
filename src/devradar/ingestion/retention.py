from dataclasses import dataclass

import psycopg

RUN_HISTORY_DAYS = 14
CLOSED_POSTING_DAYS = 30
# Deletes are capped per run so catching up on a backlog cannot flood the write-ahead log.
RUNS_PER_PRUNE = 24
JOBS_PER_PRUNE = 2000


@dataclass(frozen=True)
class Pruned:
    runs: int
    jobs: int


async def prune(conn: psycopg.AsyncConnection) -> Pruned:
    """Deletes run history older than two weeks and postings closed for over a month."""
    async with conn.transaction():
        cursor = await conn.execute(
            """
            SELECT id FROM ingestion_runs
            WHERE finished_at < now() - make_interval(days => %s)
            ORDER BY id LIMIT %s
            """,
            (RUN_HISTORY_DAYS, RUNS_PER_PRUNE),
        )
        runs = [row[0] for row in await cursor.fetchall()]
        await conn.execute("DELETE FROM board_fetches WHERE run_id = ANY(%s)", (runs,))
        await conn.execute("DELETE FROM ingestion_runs WHERE id = ANY(%s)", (runs,))

    async with conn.transaction():
        cursor = await conn.execute(
            """
            DELETE FROM jobs WHERE id IN (
                SELECT id FROM jobs
                WHERE status = 'closed' AND closed_at < now() - make_interval(days => %s)
                ORDER BY id LIMIT %s
            )
            RETURNING id
            """,
            (CLOSED_POSTING_DAYS, JOBS_PER_PRUNE),
        )
        jobs = len(await cursor.fetchall())
    return Pruned(len(runs), jobs)
