import psycopg
import pytest

from devradar.ingestion import retention
from devradar.ingestion.retention import prune

pytestmark = pytest.mark.anyio


def setup_history(conninfo):
    with psycopg.connect(conninfo) as conn:
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
        for days_ago in (20, 15, 13, 1):
            (run_id,) = conn.execute(
                """
                INSERT INTO ingestion_runs (trigger, status, started_at, finished_at)
                VALUES ('schedule', 'succeeded', now() - make_interval(days => %s),
                        now() - make_interval(days => %s))
                RETURNING id
                """,
                (days_ago, days_ago),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO board_fetches (run_id, job_board_id, status, attempts, duration_ms)
                VALUES (%s, %s, 'succeeded', 1, 10)
                """,
                (run_id, board_id),
            )
        for key, status, closed_days_ago in [
            ("open", "active", None),
            ("closed-recently", "closed", 29),
            ("closed-long-ago", "closed", 31),
        ]:
            (job_id,) = conn.execute(
                """
                INSERT INTO jobs (posting_key, company_id, title, url, is_internship, status,
                                  closed_at)
                VALUES (%s, %s, 'Intern', 'https://example.test', true, %s,
                        now() - make_interval(days => %s))
                RETURNING id
                """,
                (key, company_id, status, closed_days_ago),
            ).fetchone()
            conn.execute(
                "INSERT INTO job_countries (job_id, country) VALUES (%s, 'US')",
                (job_id,),
            )


def rows(conninfo, sql):
    with psycopg.connect(conninfo) as conn:
        return [row[0] for row in conn.execute(sql).fetchall()]


async def test_prunes_old_runs_and_long_closed_postings_only(migrated_db):
    setup_history(migrated_db)
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        assert await prune(conn) == retention.Pruned(runs=2, jobs=1)
        assert await prune(conn) == retention.Pruned(runs=0, jobs=0)

    assert rows(
        migrated_db,
        "SELECT extract(day FROM now() - finished_at)::int FROM ingestion_runs ORDER BY 1",
    ) == [1, 13]
    assert rows(migrated_db, "SELECT count(*) FROM board_fetches") == [2]
    assert rows(migrated_db, "SELECT posting_key FROM jobs ORDER BY id") == [
        "open",
        "closed-recently",
    ]
    assert rows(migrated_db, "SELECT count(*) FROM job_countries") == [2]


async def test_a_backlog_is_pruned_a_batch_at_a_time(migrated_db, monkeypatch):
    setup_history(migrated_db)
    monkeypatch.setattr(retention, "RUNS_PER_PRUNE", 1)
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        assert (await prune(conn)).runs == 1
        assert (await prune(conn)).runs == 1
        assert (await prune(conn)).runs == 0
