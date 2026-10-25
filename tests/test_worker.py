import asyncio

import httpx
import psycopg
import pytest
import respx
from psycopg_pool import AsyncConnectionPool

from devradar.ingestion import worker
from devradar.ingestion.greenhouse import API_BASE
from devradar.ingestion.worker import Ingestor, RetryPolicy, run_ingestion

pytestmark = pytest.mark.anyio

NO_WAIT = RetryPolicy(attempts=3, base_delay=0)


def job(job_id, title="Software Engineer Intern"):
    return {
        "id": job_id,
        "title": title,
        "absolute_url": f"https://example.test/{job_id}",
    }


def add_boards(conninfo, *tokens, ats="greenhouse"):
    with psycopg.connect(conninfo) as conn:
        (company_id,) = conn.execute(
            """
            INSERT INTO companies (name, slug) VALUES ('Acme', 'acme')
            ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug RETURNING id
            """
        ).fetchone()
        for token in tokens:
            conn.execute(
                """
                INSERT INTO job_boards (company_id, ats, board_token, discovered_via)
                VALUES (%s, %s, %s, 'simplify')
                """,
                (company_id, ats, token),
            )


def query(conninfo, sql):
    with psycopg.connect(conninfo) as conn:
        return conn.execute(sql).fetchall()


async def ingest(conninfo, http_concurrency=16):
    async with (
        AsyncConnectionPool(conninfo, min_size=1, max_size=4, open=False) as pool,
        httpx.AsyncClient() as client,
    ):
        ingestor = Ingestor(
            pool, client, http_concurrency=http_concurrency, retry=NO_WAIT
        )
        return await run_ingestion(pool, client, ingestor=ingestor)


@respx.mock
async def test_retries_transient_errors_and_isolates_failed_boards(migrated_db):
    add_boards(migrated_db, "ok", "flaky", "gone")
    respx.get(f"{API_BASE}/ok/jobs").respond(json={"jobs": [job(1, "Data Analyst")]})
    respx.get(f"{API_BASE}/flaky/jobs").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"jobs": []})]
    )
    gone = respx.get(f"{API_BASE}/gone/jobs").respond(404)

    summary = await ingest(migrated_db)

    assert summary.status == "partial"
    assert gone.call_count == 1
    assert query(
        migrated_db,
        """
        SELECT b.board_token, f.status, f.attempts, f.fetched_count, f.error,
               b.consecutive_failures, b.last_success_at IS NOT NULL
        FROM board_fetches f JOIN job_boards b ON b.id = f.job_board_id
        ORDER BY b.board_token
        """,
    ) == [
        ("flaky", "succeeded", 2, 0, None, 0, True),
        ("gone", "failed", 1, 0, "HTTP 404", 1, False),
        ("ok", "succeeded", 1, 1, None, 0, True),
    ]
    assert query(
        migrated_db, "SELECT status, finished_at IS NOT NULL FROM ingestion_runs"
    ) == [("partial", True)]


@respx.mock
async def test_fetches_descriptions_for_new_internships_only(migrated_db):
    add_boards(migrated_db, "acme")
    respx.get(f"{API_BASE}/acme/jobs").respond(
        json={"jobs": [job(1), job(2, "Senior Software Engineer")]}
    )
    detail = respx.get(f"{API_BASE}/acme/jobs/1").respond(
        json={"content": "&lt;p&gt;Linear programming for routing&lt;/p&gt;"}
    )

    first = await ingest(migrated_db)
    second = await ingest(migrated_db)

    assert first.outcomes[0].descriptions_fetched == 1
    assert second.outcomes[0].descriptions_fetched == 0
    assert detail.call_count == 1
    assert query(
        migrated_db, "SELECT posting_key, description FROM jobs ORDER BY id"
    ) == [
        ("greenhouse:1", "Linear programming for routing"),
        ("greenhouse:2", None),
    ]


@respx.mock
async def test_failed_description_is_retried_on_next_run(migrated_db):
    add_boards(migrated_db, "acme")
    respx.get(f"{API_BASE}/acme/jobs").respond(json={"jobs": [job(1)]})
    detail = respx.get(f"{API_BASE}/acme/jobs/1")
    detail.side_effect = [
        httpx.Response(404),
        httpx.Response(200, json={"content": "ok"}),
    ]

    first = await ingest(migrated_db)
    second = await ingest(migrated_db)

    assert (first.status, first.outcomes[0].descriptions_failed) == ("succeeded", 1)
    assert second.outcomes[0].descriptions_fetched == 1


@respx.mock
async def test_board_is_disabled_after_repeated_failures(migrated_db):
    add_boards(migrated_db, "gone")
    respx.get(f"{API_BASE}/gone/jobs").respond(404)
    for _ in range(worker.DISABLE_AFTER_FAILURES):
        await ingest(migrated_db)
    assert query(
        migrated_db, "SELECT consecutive_failures, enabled FROM job_boards"
    ) == [(5, False)]
    assert (await ingest(migrated_db)).outcomes == []


@respx.mock
async def test_unexpected_error_on_one_board_does_not_stop_the_others(
    migrated_db, monkeypatch
):
    add_boards(migrated_db, "buggy", "ok")
    respx.get(f"{API_BASE}/ok/jobs").respond(json={"jobs": [job(1)]})
    respx.get(f"{API_BASE}/ok/jobs/1").respond(json={"content": "x"})
    real_fetch = worker.ADAPTERS["greenhouse"].fetch_postings

    async def fetch_postings(client, board):
        if board.token == "buggy":
            raise KeyError("surprise")
        return await real_fetch(client, board)

    monkeypatch.setattr(worker.ADAPTERS["greenhouse"], "fetch_postings", fetch_postings)
    summary = await ingest(migrated_db)

    assert {o.board.token: (o.status, o.error) for o in summary.outcomes} == {
        "buggy": ("failed", "KeyError: 'surprise'"),
        "ok": ("succeeded", None),
    }


@respx.mock
async def test_concurrent_requests_never_exceed_the_limit(migrated_db):
    tokens = [f"board{i}" for i in range(8)]
    add_boards(migrated_db, *tokens)
    in_flight = peak = 0

    async def slow_board(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200, json={"jobs": []})

    respx.get(url__regex=rf"{API_BASE}/board\d/jobs").mock(side_effect=slow_board)
    summary = await ingest(migrated_db, http_concurrency=3)

    assert summary.status == "succeeded"
    assert peak == 3


@respx.mock
async def test_each_ats_gets_its_own_request_limit(migrated_db):
    add_boards(migrated_db, "g0", "g1", "g2", "g3")
    add_boards(migrated_db, "a0", "a1", "a2", "a3", ats="ashby")
    in_flight = {"greenhouse": 0, "ashby": 0}
    peak = {"greenhouse": 0, "ashby": 0}

    def slow(ats, body):
        async def respond(request):
            in_flight[ats] += 1
            peak[ats] = max(peak[ats], in_flight[ats])
            await asyncio.sleep(0.02)
            in_flight[ats] -= 1
            return httpx.Response(200, json=body)

        return respond

    respx.get(url__regex=rf"{API_BASE}/g\d/jobs").mock(
        side_effect=slow("greenhouse", {"jobs": []})
    )
    respx.get(url__regex=r"https://api.ashbyhq.com/posting-api/job-board/a\d").mock(
        side_effect=slow("ashby", {"jobs": []})
    )
    await ingest(migrated_db, http_concurrency=2)
    assert peak == {"greenhouse": 2, "ashby": 2}


@respx.mock
async def test_duration_excludes_time_spent_waiting_for_a_slot(migrated_db):
    add_boards(migrated_db, *(f"board{i}" for i in range(4)))

    async def slow_board(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"jobs": []})

    respx.get(url__regex=rf"{API_BASE}/board\d/jobs").mock(side_effect=slow_board)
    summary = await ingest(migrated_db, http_concurrency=1)

    assert summary.duration_s >= 0.2
    assert max(outcome.duration_ms for outcome in summary.outcomes) < 120
