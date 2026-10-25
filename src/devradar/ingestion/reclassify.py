import asyncio
from dataclasses import dataclass

import psycopg

from devradar.config import settings
from devradar.ingestion.classify import categorize, is_internship
from devradar.ingestion.store import replace_categories
from devradar.ingestion.worker import ADAPTERS


@dataclass
class ReclassifyStats:
    jobs: int = 0
    became_internship: int = 0
    stopped_being_internship: int = 0
    categories_changed: int = 0


async def reclassify(conn: psycopg.AsyncConnection) -> ReclassifyStats:
    stats = ReclassifyStats()
    cursor = await conn.execute(
        """
        SELECT j.id, j.title, j.description, j.is_internship, s.source, s.raw,
               coalesce(array_agg(c.slug) FILTER (WHERE c.slug IS NOT NULL), '{}')
        FROM jobs j
        JOIN LATERAL (
            SELECT source, raw FROM job_sources WHERE job_id = j.id ORDER BY id LIMIT 1
        ) s ON true
        LEFT JOIN job_categories jc ON jc.job_id = j.id
        LEFT JOIN categories c ON c.id = jc.category_id
        GROUP BY j.id, s.source, s.raw
        """
    )
    rows = await cursor.fetchall()

    async with conn.transaction():
        for job_id, title, description, was_internship, source, raw, current in rows:
            stats.jobs += 1
            employment_type = ADAPTERS[source].parse(raw).employment_type
            internship = is_internship(title, employment_type)
            categories = categorize(title, description)

            if internship != was_internship:
                await conn.execute(
                    "UPDATE jobs SET is_internship = %s WHERE id = %s",
                    (internship, job_id),
                )
                if internship:
                    stats.became_internship += 1
                else:
                    stats.stopped_being_internship += 1
            if categories != set(current):
                await replace_categories(conn, job_id, categories)
                stats.categories_changed += 1
    return stats


async def _main() -> ReclassifyStats:
    async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        return await reclassify(conn)


def main() -> None:
    stats = asyncio.run(_main())
    print(
        f"reclassified {stats.jobs} jobs: {stats.categories_changed} category changes, "
        f"{stats.became_internship} became internships, "
        f"{stats.stopped_being_internship} stopped being internships"
    )
