import httpx
import psycopg
import pytest
import respx

from devradar.discovery.lists import ListSource
from devradar.discovery.run import discover

pytestmark = pytest.mark.anyio

LIST_A = ListSource("simplify", "https://lists.test/a.json")
LIST_B = ListSource("vansh", "https://lists.test/b.json", shortened_links=True)


def listing(company, url):
    return {"company_name": company, "url": url, "title": "Intern"}


async def run_discovery(conninfo, sources):
    async with (
        await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn,
        httpx.AsyncClient() as client,
    ):
        return await discover(conn, client, sources)


def boards(conninfo):
    with psycopg.connect(conninfo) as conn:
        return conn.execute(
            """
            SELECT c.name, b.ats, b.api_region, b.board_token, b.discovered_via
            FROM job_boards b JOIN companies c ON c.id = b.company_id
            ORDER BY b.board_token
            """
        ).fetchall()


@respx.mock
async def test_creates_boards_and_skips_unpollable_listings(migrated_db):
    respx.get(LIST_A.url).respond(
        json=[
            listing("Stripe", "https://job-boards.greenhouse.io/stripe/jobs/1"),
            listing("Stripe", "https://job-boards.greenhouse.io/stripe/jobs/2"),
            listing("Cirrus", "https://jobs.eu.lever.co/cirrus/abc/apply"),
            listing("Cisco", "https://careers.cisco.com/global/en/job/2008901"),
            {"company_name": "", "url": "https://jobs.ashbyhq.com/x/1"},
        ]
    )
    [stats] = await run_discovery(migrated_db, (LIST_A,))
    assert (
        stats.listings,
        stats.boards_seen,
        stats.new_boards,
        stats.without_board,
    ) == (4, 2, 2, 1)
    assert boards(migrated_db) == [
        ("Cirrus", "lever", "eu", "cirrus", "simplify"),
        ("Stripe", "greenhouse", "global", "stripe", "simplify"),
    ]


@respx.mock
async def test_rerun_creates_nothing(migrated_db):
    respx.get(LIST_A.url).respond(
        json=[listing("Stripe", "https://job-boards.greenhouse.io/stripe/jobs/1")]
    )
    await run_discovery(migrated_db, (LIST_A,))
    [stats] = await run_discovery(migrated_db, (LIST_A,))
    assert (stats.boards_seen, stats.new_boards, stats.new_companies) == (1, 0, 0)
    assert len(boards(migrated_db)) == 1


@respx.mock
async def test_known_board_keeps_its_company_when_another_list_names_it_differently(
    migrated_db,
):
    respx.get(LIST_A.url).respond(
        json=[listing("Meta", "https://jobs.lever.co/meta/1")]
    )
    respx.get(LIST_B.url).respond(json=[listing("Meta Platforms", "https://go.test/m")])
    respx.get("https://go.test/m").respond(
        302, headers={"location": "https://jobs.lever.co/meta/2"}
    )
    await run_discovery(migrated_db, (LIST_A, LIST_B))
    assert boards(migrated_db) == [("Meta", "lever", "global", "meta", "simplify")]


@respx.mock
async def test_boards_with_equivalent_company_names_share_one_company(migrated_db):
    respx.get(LIST_A.url).respond(
        json=[
            listing("Stripe", "https://job-boards.greenhouse.io/stripe/jobs/1"),
            listing("Stripe, Inc.", "https://jobs.ashbyhq.com/stripe-interns/1"),
        ]
    )
    [stats] = await run_discovery(migrated_db, (LIST_A,))
    assert (stats.new_boards, stats.new_companies) == (2, 1)


@respx.mock
async def test_short_links_are_resolved_before_parsing(migrated_db):
    respx.get(LIST_B.url).respond(
        json=[
            listing("Acme", "https://go.test/ok"),
            listing("Gone", "https://go.test/dead"),
        ]
    )
    respx.get("https://go.test/ok").respond(
        302, headers={"location": "https://jobs.ashbyhq.com/Acme/123"}
    )
    respx.get("https://go.test/dead").respond(404)
    [stats] = await run_discovery(migrated_db, (LIST_B,))
    assert (stats.new_boards, stats.unresolved_links) == (1, 1)
    assert boards(migrated_db) == [("Acme", "ashby", "global", "acme", "vansh")]
