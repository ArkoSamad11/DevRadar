from dataclasses import dataclass, field

import psycopg
from psycopg.types.json import Jsonb

from devradar.ingestion.classify import categorize, is_internship, is_remote
from devradar.ingestion.models import Board, Posting

MISSES_BEFORE_CLOSE = 2
BOARD_LOCK_NAMESPACE = 1


@dataclass(frozen=True)
class DescriptionTask:
    job_id: int
    external_id: str
    title: str


@dataclass
class StoreResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    closed: int = 0
    reopened: int = 0
    inline_descriptions: int = 0
    needs_description: list[DescriptionTask] = field(default_factory=list)


@dataclass(frozen=True)
class _KnownSource:
    job_id: int
    content_hash: str
    has_description: bool


async def store_postings(
    conn: psycopg.AsyncConnection, board: Board, postings: list[Posting]
) -> StoreResult:
    result = StoreResult()
    unique = list({posting.external_id: posting for posting in postings}.values())
    external_ids = [posting.external_id for posting in unique]

    async with conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", (BOARD_LOCK_NAMESPACE, board.id)
        )
        known = await _known_sources(conn, board.ats, external_ids)

        seen_job_ids = []
        for posting in unique:
            internship = is_internship(posting.title, posting.employment_type)
            existing = known.get(posting.external_id)
            changed = existing is None or existing.content_hash != posting.content_hash

            if changed:
                job_id = await _write_posting(conn, board, posting, internship)
                if existing is None:
                    result.inserted += 1
                else:
                    result.updated += 1
            else:
                job_id = existing.job_id
                result.unchanged += 1
            seen_job_ids.append(job_id)

            if internship and (changed or not existing.has_description):
                task = DescriptionTask(job_id, posting.external_id, posting.title)
                if posting.description is not None:
                    await store_description(conn, task, posting.description)
                    result.inline_descriptions += 1
                else:
                    result.needs_description.append(task)

        await conn.execute(
            """
            UPDATE job_sources SET missed_fetches = 0
            WHERE source = %s AND external_id = ANY(%s) AND missed_fetches > 0
            """,
            (board.ats, external_ids),
        )
        result.reopened = await _count(
            conn,
            """
            UPDATE jobs SET status = 'active', closed_at = NULL
            WHERE id = ANY(%s) AND status = 'closed'
            RETURNING id
            """,
            (seen_job_ids,),
        )
        result.closed = await _record_misses(conn, board, external_ids)
    return result


async def store_description(
    conn: psycopg.AsyncConnection, task: DescriptionTask, description: str
) -> None:
    async with conn.transaction():
        await conn.execute(
            "UPDATE jobs SET description = %s WHERE id = %s", (description, task.job_id)
        )
        await replace_categories(conn, task.job_id, categorize(task.title, description))


async def _known_sources(
    conn: psycopg.AsyncConnection, source: str, external_ids: list[str]
) -> dict[str, _KnownSource]:
    cursor = await conn.execute(
        """
        SELECT s.external_id, s.job_id, s.content_hash, j.description IS NOT NULL
        FROM job_sources s JOIN jobs j ON j.id = s.job_id
        WHERE s.source = %s AND s.external_id = ANY(%s)
        """,
        (source, external_ids),
    )
    return {row[0]: _KnownSource(*row[1:]) for row in await cursor.fetchall()}


async def _write_posting(
    conn: psycopg.AsyncConnection, board: Board, posting: Posting, internship: bool
) -> int:
    cursor = await conn.execute(
        """
        INSERT INTO jobs (posting_key, company_id, title, url, is_internship, posted_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (posting_key) DO UPDATE
            SET title = EXCLUDED.title,
                url = EXCLUDED.url,
                is_internship = EXCLUDED.is_internship,
                posted_at = EXCLUDED.posted_at
        RETURNING id
        """,
        (
            posting.posting_key,
            board.company_id,
            posting.title,
            posting.url,
            internship,
            posting.posted_at,
        ),
    )
    (job_id,) = await cursor.fetchone()

    await conn.execute(
        """
        INSERT INTO job_sources (job_id, job_board_id, source, external_id, url, raw, content_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, external_id) DO UPDATE
            SET job_board_id = EXCLUDED.job_board_id,
                url = EXCLUDED.url,
                raw = EXCLUDED.raw,
                content_hash = EXCLUDED.content_hash
        """,
        (
            job_id,
            board.id,
            posting.source,
            posting.external_id,
            posting.url,
            Jsonb(posting.raw),
            posting.content_hash,
        ),
    )

    await conn.execute("DELETE FROM job_locations WHERE job_id = %s", (job_id,))
    async with conn.cursor() as cursor:
        await cursor.executemany(
            "INSERT INTO job_locations (job_id, raw, is_remote) VALUES (%s, %s, %s)",
            [
                (job_id, location, is_remote(location))
                for location in dict.fromkeys(posting.locations)
            ],
        )

    await replace_categories(conn, job_id, categorize(posting.title))
    return job_id


async def replace_categories(
    conn: psycopg.AsyncConnection, job_id: int, categories: set[str]
) -> None:
    await conn.execute("DELETE FROM job_categories WHERE job_id = %s", (job_id,))
    await conn.execute(
        """
        INSERT INTO job_categories (job_id, category_id)
        SELECT %s, id FROM categories WHERE slug = ANY(%s)
        """,
        (job_id, sorted(categories)),
    )


async def _record_misses(
    conn: psycopg.AsyncConnection, board: Board, seen_external_ids: list[str]
) -> int:
    cursor = await conn.execute(
        """
        UPDATE job_sources SET missed_fetches = missed_fetches + 1
        WHERE job_board_id = %s AND NOT (external_id = ANY(%s))
        RETURNING job_id
        """,
        (board.id, seen_external_ids),
    )
    missed_job_ids = [row[0] for row in await cursor.fetchall()]
    return await _count(
        conn,
        """
        UPDATE jobs j SET status = 'closed', closed_at = now()
        WHERE j.id = ANY(%s) AND j.status = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM job_sources s
              WHERE s.job_id = j.id AND s.missed_fetches < %s
          )
        RETURNING j.id
        """,
        (missed_job_ids, MISSES_BEFORE_CLOSE),
    )


async def _count(conn: psycopg.AsyncConnection, query: str, params: tuple) -> int:
    cursor = await conn.execute(query, params)
    return len(await cursor.fetchall())
