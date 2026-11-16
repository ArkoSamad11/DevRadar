from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest

from devradar.api.main import create_app
from devradar.db import create_pool
from devradar.ingestion.models import Board, Posting
from devradar.ingestion.store import store_postings

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)


def posting(
    external_id, title, hours_ago, locations=("New York, NY",), description=None
):
    return Posting(
        source="ashby",
        external_id=external_id,
        title=title,
        url=f"https://jobs.ashbyhq.com/acme/{external_id}",
        locations=locations,
        posted_at=NOW - timedelta(hours=hours_ago),
        employment_type=None,
        raw={"id": external_id, "title": title},
        description=description,
    )


def make_board(conninfo, slug):
    with psycopg.connect(conninfo) as conn:
        (company_id,) = conn.execute(
            "INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id",
            (slug.title(), slug),
        ).fetchone()
        (board_id,) = conn.execute(
            """
            INSERT INTO job_boards (company_id, ats, board_token, discovered_via)
            VALUES (%s, 'ashby', %s, 'simplify') RETURNING id
            """,
            (company_id, slug),
        ).fetchone()
    return Board(board_id, company_id, "ashby", slug)


async def seed(conninfo, board, postings):
    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        await store_postings(conn, board, postings)


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
async def catalog(migrated_db):
    acme, globex = make_board(migrated_db, "acme"), make_board(migrated_db, "globex")
    await seed(
        migrated_db,
        acme,
        [
            posting("a1", "Software Engineer Intern", 1, description="Python and Go."),
            posting(
                "a2", "ML Research Intern", 2, ("Remote - US",), "PyTorch, 100% remote."
            ),
            posting("a3", "Senior Software Engineer", 3),
            posting("a4", "Quant Trader Intern", 4, ("Chicago, IL",)),
        ],
    )
    await seed(
        migrated_db,
        globex,
        [
            posting("g1", "Security Engineer Intern", 5, ("New York, NY", "Remote")),
            posting(
                "g2",
                "Data Science Intern",
                6,
                ("Austin, TX",),
                "SQL and 100 dashboards",
            ),
        ],
    )
    return migrated_db


def titles(response):
    assert response.status_code == 200, response.text
    return [item["title"] for item in response.json()["items"]]


async def test_lists_active_internships_newest_first(api, catalog):
    assert titles(await api.get("/jobs")) == [
        "Software Engineer Intern",
        "ML Research Intern",
        "Quant Trader Intern",
        "Security Engineer Intern",
        "Data Science Intern",
    ]


async def test_closed_jobs_are_hidden_unless_requested(api, catalog):
    with psycopg.connect(catalog) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'closed', closed_at = now() WHERE title = 'Quant Trader Intern'"
        )
    assert "Quant Trader Intern" not in titles(await api.get("/jobs"))
    assert "Quant Trader Intern" in titles(await api.get("/jobs?include_closed=true"))


async def test_non_internships_are_included_on_request(api, catalog):
    assert "Senior Software Engineer" in titles(
        await api.get("/jobs?internships_only=false")
    )


async def test_cursor_pages_cover_every_job_exactly_once(api, catalog):
    seen, cursor = [], None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        body = (await api.get("/jobs", params=params)).json()
        seen += [item["id"] for item in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    all_ids = [
        item["id"] for item in (await api.get("/jobs?limit=100")).json()["items"]
    ]
    assert seen == all_ids and len(set(seen)) == 5


async def test_new_postings_do_not_shift_later_pages(api, catalog):
    first = (await api.get("/jobs?limit=2")).json()
    await seed(
        catalog, make_board(catalog, "initech"), [posting("n1", "New Intern", 0)]
    )
    second = (
        await api.get("/jobs", params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()
    assert [i["title"] for i in second["items"]] == [
        "Quant Trader Intern",
        "Security Engineer Intern",
    ]


async def test_ties_on_listed_at_are_broken_by_id(api, migrated_db):
    board = make_board(migrated_db, "acme")
    await seed(
        migrated_db, board, [posting(f"t{i}", f"Intern {i}", 1) for i in range(5)]
    )
    seen, cursor = [], None
    for _ in range(3):
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        body = (await api.get("/jobs", params=params)).json()
        seen += [item["title"] for item in body["items"]]
        cursor = body["next_cursor"]
    assert sorted(seen) == [f"Intern {i}" for i in range(5)]
    assert cursor is None


async def test_keyword_matches_title_or_description(api, catalog):
    assert titles(await api.get("/jobs?q=python")) == ["Software Engineer Intern"]
    assert titles(await api.get("/jobs?q=QUANT")) == ["Quant Trader Intern"]


async def test_keyword_wildcards_are_literal(api, catalog):
    assert titles(await api.get("/jobs", params={"q": "100%"})) == [
        "ML Research Intern"
    ]
    assert titles(await api.get("/jobs", params={"q": "_ntern"})) == []


async def test_category_filter_matches_any_requested_category(api, catalog):
    response = await api.get("/jobs?category=quant&category=security")
    assert titles(response) == ["Quant Trader Intern", "Security Engineer Intern"]


async def test_location_remote_and_company_filters(api, catalog):
    assert titles(await api.get("/jobs?location=new york")) == [
        "Software Engineer Intern",
        "Security Engineer Intern",
    ]
    assert titles(await api.get("/jobs?remote=true")) == [
        "ML Research Intern",
        "Security Engineer Intern",
    ]
    assert "ML Research Intern" not in titles(await api.get("/jobs?remote=false"))
    assert titles(await api.get("/jobs?company=globex")) == [
        "Security Engineer Intern",
        "Data Science Intern",
    ]


@pytest.mark.parametrize(
    ("query", "expected_status"),
    [
        ("category=nope", 422),
        ("limit=0", 422),
        ("limit=101", 422),
        ("q=a", 422),
        ("cursor=not-a-cursor", 400),
        ("cursor=WyIyMDI2LTAxLTAxVDAwOjAwOjAwIiwgMV0", 400),
    ],
)
async def test_invalid_parameters_are_rejected(api, catalog, query, expected_status):
    assert (await api.get(f"/jobs?{query}")).status_code == expected_status


async def test_job_detail_includes_description_and_sources(api, catalog):
    [item] = (await api.get("/jobs?q=pytorch")).json()["items"]
    detail = (await api.get(f"/jobs/{item['id']}")).json()
    assert detail["description"] == "PyTorch, 100% remote."
    assert detail["categories"] == ["ml_ai"]
    assert detail["sources"] == [
        {
            "source": "ashby",
            "external_id": "a2",
            "url": "https://jobs.ashbyhq.com/acme/a2",
        }
    ]


async def test_unknown_job_is_404(api, catalog):
    assert (await api.get("/jobs/999999")).status_code == 404
