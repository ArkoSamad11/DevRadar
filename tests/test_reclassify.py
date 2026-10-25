import psycopg
import pytest

from devradar.ingestion.greenhouse import parse_posting
from devradar.ingestion.models import Board
from devradar.ingestion.reclassify import reclassify
from devradar.ingestion.store import store_postings

pytestmark = pytest.mark.anyio


def raw_job(job_id, title, employment_type=None):
    metadata = (
        [{"name": "Employment Type", "value": employment_type}]
        if employment_type
        else []
    )
    return {
        "id": job_id,
        "title": title,
        "absolute_url": f"https://example.test/{job_id}",
        "metadata": metadata,
    }


async def test_reclassify_fixes_stale_labels_and_only_writes_changes(migrated_db):
    with psycopg.connect(migrated_db) as conn:
        (company_id,) = conn.execute(
            "INSERT INTO companies (name, slug) VALUES ('Acme', 'acme') RETURNING id"
        ).fetchone()
        (board_id,) = conn.execute(
            """
            INSERT INTO job_boards (company_id, ats, board_token, discovered_via)
            VALUES (%s, 'greenhouse', 'acme', 'simplify') RETURNING id
            """,
            (company_id,),
        ).fetchone()
    board = Board(board_id, company_id, "greenhouse", "acme")
    postings = [
        parse_posting(raw_job(1, "Cybersecurity Analyst", "Summer Intern")),
        parse_posting(raw_job(2, "Software Engineer Intern")),
    ]

    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        await store_postings(conn, board, postings)
        await conn.execute(
            "UPDATE jobs SET is_internship = false WHERE posting_key = 'greenhouse:1'"
        )
        await conn.execute("DELETE FROM job_categories")
        await conn.commit()

        stats = await reclassify(conn)
        assert (stats.jobs, stats.became_internship, stats.categories_changed) == (
            2,
            1,
            2,
        )
        assert (await reclassify(conn)).categories_changed == 0

    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            """
            SELECT j.posting_key, j.is_internship, c.slug FROM jobs j
            JOIN job_categories jc ON jc.job_id = j.id JOIN categories c ON c.id = jc.category_id
            ORDER BY 1
            """
        ).fetchall() == [
            ("greenhouse:1", True, "security"),
            ("greenhouse:2", True, "software"),
        ]
