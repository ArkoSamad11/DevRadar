import httpx
import psycopg
import pytest

from devradar.api.main import create_app
from devradar.db import create_pool

pytestmark = pytest.mark.anyio


@pytest.fixture
async def api(migrated_db):
    app = create_app()
    async with create_pool(migrated_db) as pool:
        app.state.pool = pool
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield client


@pytest.fixture
def catalog(migrated_db):
    with psycopg.connect(migrated_db) as conn:
        companies = {}
        for name, slug in [
            ("Acme", "acme"),
            ("Globex", "globex"),
            ("Initech", "initech"),
        ]:
            (companies[slug],) = conn.execute(
                "INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id",
                (name, slug),
            ).fetchone()
        boards = {}
        for slug, ats, enabled in [
            ("acme", "greenhouse", True),
            ("globex", "lever", True),
            ("initech", "workday", True),
            ("initech", "greenhouse", False),
        ]:
            (boards[slug, ats],) = conn.execute(
                """
                INSERT INTO job_boards (company_id, ats, board_token, discovered_via, enabled)
                VALUES (%s, %s, %s, 'simplify', %s) RETURNING id
                """,
                (companies[slug], ats, slug, enabled),
            ).fetchone()
        for key, slug, internship, status in [
            ("greenhouse:1", "acme", True, "active"),
            ("greenhouse:2", "acme", False, "active"),
            ("greenhouse:3", "acme", True, "closed"),
            ("lever:1", "globex", False, "active"),
        ]:
            (job_id,) = conn.execute(
                """
                INSERT INTO jobs (posting_key, company_id, title, url, is_internship,
                                  status, closed_at)
                VALUES (%s, %s, 'Job', 'https://x.test', %s, %s,
                        CASE WHEN %s = 'closed' THEN now() END)
                RETURNING id
                """,
                (key, companies[slug], internship, status, status),
            ).fetchone()
            source, external_id = key.split(":")
            conn.execute(
                """
                INSERT INTO job_sources (job_id, job_board_id, source, external_id, url, raw,
                                         content_hash)
                VALUES (%s, %s, %s, %s, 'https://x.test', '{}', 'h')
                """,
                (job_id, boards[slug, source], source, external_id),
            )
    return migrated_db


async def test_companies_report_active_job_and_internship_counts(api, catalog):
    body = (await api.get("/companies")).json()
    assert [
        (c["slug"], c["active_jobs"], c["active_internships"]) for c in body["items"]
    ] == [
        ("acme", 2, 1),
        ("globex", 1, 0),
        ("initech", 0, 0),
    ]
    assert body["items"][2]["boards"] == [
        {"ats": "greenhouse", "token": "initech"},
        {"ats": "workday", "token": "initech"},
    ]


async def test_companies_filter_by_name_and_hiring_interns(api, catalog):
    assert [
        c["slug"] for c in (await api.get("/companies?q=GLOB")).json()["items"]
    ] == ["globex"]
    hiring = (await api.get("/companies?hiring_interns=true")).json()["items"]
    assert [c["slug"] for c in hiring] == ["acme"]


async def test_companies_paginate_by_slug(api, catalog):
    first = (await api.get("/companies?limit=2")).json()
    second = (
        await api.get("/companies", params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()
    assert [c["slug"] for c in first["items"] + second["items"]] == [
        "acme",
        "globex",
        "initech",
    ]
    assert second["next_cursor"] is None


async def test_sources_report_board_health_and_support(api, catalog):
    sources = {s["ats"]: s for s in (await api.get("/sources")).json()}
    assert (
        sources["greenhouse"]["boards"],
        sources["greenhouse"]["enabled_boards"],
    ) == (2, 1)
    assert sources["greenhouse"]["active_jobs"] == 2
    assert (sources["workday"]["supported"], sources["workday"]["active_jobs"]) == (
        False,
        0,
    )
    assert sources["lever"]["supported"] is True


async def test_ingestion_runs_aggregate_board_fetches_newest_first(api, catalog):
    with psycopg.connect(catalog) as conn:
        run_ids = [
            conn.execute(
                "INSERT INTO ingestion_runs (trigger, status) VALUES ('schedule', %s) RETURNING id",
                (status,),
            ).fetchone()[0]
            for status in ("succeeded", "partial", "running")
        ]
        board_ids = [
            row[0] for row in conn.execute("SELECT id FROM job_boards ORDER BY id")
        ]
        for board_id, status, fetched in [
            (board_ids[0], "succeeded", 10),
            (board_ids[1], "failed", 0),
        ]:
            conn.execute(
                """
                INSERT INTO board_fetches (run_id, job_board_id, status, attempts, fetched_count,
                                           inserted_count, closed_count, duration_ms)
                VALUES (%s, %s, %s, 1, %s, %s, 1, 5)
                """,
                (run_ids[1], board_id, status, fetched, fetched),
            )

    first = (await api.get("/ingestion-runs?limit=2")).json()
    assert [(r["id"], r["status"]) for r in first["items"]] == [
        (run_ids[2], "running"),
        (run_ids[1], "partial"),
    ]
    partial = first["items"][1]
    assert (
        partial["boards"],
        partial["failed_boards"],
        partial["postings_fetched"],
    ) == (2, 1, 10)
    assert (partial["inserted"], partial["closed"]) == (10, 2)

    rest = (
        await api.get("/ingestion-runs", params={"cursor": first["next_cursor"]})
    ).json()
    assert [r["id"] for r in rest["items"]] == [run_ids[0]]
    assert rest["items"][0]["boards"] == 0
