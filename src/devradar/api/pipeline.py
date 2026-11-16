from typing import Annotated

from fastapi import APIRouter, Query
from psycopg.rows import dict_row

from devradar.api.deps import Connection
from devradar.api.pagination import decode_cursor, encode_cursor
from devradar.api.schemas import IngestionRun, IngestionRunPage, SourceStatus
from devradar.ingestion.worker import ADAPTERS

router = APIRouter(tags=["pipeline"])


@router.get("/sources", response_model=list[SourceStatus])
async def list_sources(conn: Connection) -> list[SourceStatus]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT b.ats,
                   count(*) AS boards,
                   count(*) FILTER (WHERE b.enabled) AS enabled_boards,
                   max(b.last_success_at) AS last_success_at,
                   (SELECT count(*) FROM job_sources s JOIN jobs j ON j.id = s.job_id
                    WHERE s.source = b.ats AND j.status = 'active') AS active_jobs
            FROM job_boards b
            GROUP BY b.ats
            ORDER BY b.ats
            """
        )
        rows = await cur.fetchall()
    return [SourceStatus(**row, supported=row["ats"] in ADAPTERS) for row in rows]


@router.get("/ingestion-runs", response_model=IngestionRunPage)
async def list_ingestion_runs(
    conn: Connection,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> IngestionRunPage:
    (before_id,) = decode_cursor(cursor, int) if cursor else (None,)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            WITH page AS (
                SELECT * FROM ingestion_runs
                WHERE %(before_id)s::bigint IS NULL OR id < %(before_id)s
                ORDER BY id DESC
                LIMIT %(fetch)s
            )
            SELECT r.id, r.trigger, r.status, r.started_at, r.finished_at,
                   count(f.id) AS boards,
                   count(f.id) FILTER (WHERE f.status = 'failed') AS failed_boards,
                   coalesce(sum(f.fetched_count), 0) AS postings_fetched,
                   coalesce(sum(f.inserted_count), 0) AS inserted,
                   coalesce(sum(f.updated_count), 0) AS updated,
                   coalesce(sum(f.closed_count), 0) AS closed,
                   coalesce(sum(f.rejected_count), 0) AS rejected,
                   coalesce(sum(f.descriptions_fetched), 0) AS descriptions_stored
            FROM page r
            LEFT JOIN board_fetches f ON f.run_id = r.id
            GROUP BY r.id, r.trigger, r.status, r.started_at, r.finished_at
            ORDER BY r.id DESC
            """,
            {"before_id": before_id, "fetch": limit + 1},
        )
        rows = await cur.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    return IngestionRunPage(
        items=[IngestionRun(**row) for row in rows],
        limit=limit,
        next_cursor=encode_cursor(rows[-1]["id"]) if has_more else None,
    )
