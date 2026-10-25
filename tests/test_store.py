import psycopg
import pytest

from devradar.ingestion.models import Board, Posting
from devradar.ingestion.store import store_description, store_postings

pytestmark = pytest.mark.anyio


def posting(
    external_id,
    title="Software Engineer Intern",
    locations=("New York, NY",),
    description=None,
    **raw,
):
    return Posting(
        source="greenhouse",
        external_id=external_id,
        title=title,
        url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
        locations=locations,
        posted_at=None,
        employment_type=None,
        raw={"id": external_id, "title": title, **raw},
        description=description,
    )


@pytest.fixture
def board(migrated_db):
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
    return Board(board_id, company_id, "greenhouse", "acme")


async def store(conninfo, board, postings):
    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        return await store_postings(conn, board, postings)


def query(conninfo, sql):
    with psycopg.connect(conninfo) as conn:
        return conn.execute(sql).fetchall()


async def test_first_store_inserts_postings_with_locations_and_categories(
    migrated_db, board
):
    result = await store(
        migrated_db,
        board,
        [
            posting("1", locations=("New York, NY", "Remote - US")),
            posting("2", title="Senior Data Scientist"),
        ],
    )
    assert (result.inserted, result.updated, result.unchanged) == (2, 0, 0)
    assert [task.external_id for task in result.needs_description] == ["1"]
    assert query(
        migrated_db, "SELECT posting_key, is_internship, status FROM jobs ORDER BY id"
    ) == [
        ("greenhouse:1", True, "active"),
        ("greenhouse:2", False, "active"),
    ]
    assert query(
        migrated_db, "SELECT raw, is_remote FROM job_locations ORDER BY raw"
    ) == [
        ("New York, NY", False),
        ("New York, NY", False),
        ("Remote - US", True),
    ]
    assert query(
        migrated_db,
        """
        SELECT j.posting_key, c.slug FROM job_categories jc
        JOIN jobs j ON j.id = jc.job_id JOIN categories c ON c.id = jc.category_id
        ORDER BY 1
        """,
    ) == [("greenhouse:1", "software"), ("greenhouse:2", "data_science")]


async def test_rerun_with_same_postings_writes_nothing_new(migrated_db, board):
    await store(migrated_db, board, [posting("1"), posting("2")])
    result = await store(migrated_db, board, [posting("1"), posting("2")])
    assert (result.inserted, result.updated, result.unchanged) == (0, 0, 2)
    assert query(migrated_db, "SELECT count(*) FROM jobs") == [(2,)]
    assert query(migrated_db, "SELECT count(*) FROM job_sources") == [(2,)]


async def test_rerun_does_not_rewrite_unchanged_rows(migrated_db, board):
    await store(migrated_db, board, [posting("1"), posting("2")])
    versions = (
        "SELECT xmin::text FROM job_sources UNION ALL SELECT xmin::text FROM jobs"
    )
    before = query(migrated_db, versions)
    await store(migrated_db, board, [posting("1"), posting("2")])
    assert query(migrated_db, versions) == before


async def test_internship_description_is_requested_until_stored(migrated_db, board):
    [task] = (await store(migrated_db, board, [posting("1")])).needs_description
    assert len((await store(migrated_db, board, [posting("1")])).needs_description) == 1

    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        await store_description(conn, task, "Build mixed-integer models for routing.")
    assert (await store(migrated_db, board, [posting("1")])).needs_description == []
    assert query(
        migrated_db,
        "SELECT c.slug FROM job_categories jc JOIN categories c ON c.id = jc.category_id ORDER BY 1",
    ) == [("operations_research",), ("software",)]


async def test_edited_posting_is_updated_and_description_refetched(migrated_db, board):
    [task] = (await store(migrated_db, board, [posting("1")])).needs_description
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        await store_description(conn, task, "Old description")

    result = await store(
        migrated_db, board, [posting("1", title="Backend Engineer Intern")]
    )
    assert (result.inserted, result.updated) == (0, 1)
    assert [t.title for t in result.needs_description] == ["Backend Engineer Intern"]
    assert query(migrated_db, "SELECT title FROM jobs") == [
        ("Backend Engineer Intern",)
    ]


async def test_job_closes_after_two_consecutive_misses_and_reopens_when_seen(
    migrated_db, board
):
    await store(migrated_db, board, [posting("1"), posting("2")])

    assert (await store(migrated_db, board, [posting("1")])).closed == 0
    assert (await store(migrated_db, board, [posting("1")])).closed == 1
    assert query(
        migrated_db,
        "SELECT posting_key, status, closed_at IS NOT NULL FROM jobs ORDER BY id",
    ) == [("greenhouse:1", "active", False), ("greenhouse:2", "closed", True)]

    assert (await store(migrated_db, board, [posting("1"), posting("2")])).reopened == 1
    assert query(
        migrated_db,
        "SELECT status, missed_fetches FROM jobs j JOIN job_sources s ON s.job_id = j.id WHERE posting_key = 'greenhouse:2'",
    ) == [("active", 0)]


async def test_a_seen_posting_resets_the_miss_count(migrated_db, board):
    await store(migrated_db, board, [posting("1"), posting("2")])
    await store(migrated_db, board, [posting("1")])
    await store(migrated_db, board, [posting("1"), posting("2")])
    assert (await store(migrated_db, board, [posting("1")])).closed == 0


async def test_duplicate_postings_in_one_response_are_stored_once(migrated_db, board):
    result = await store(migrated_db, board, [posting("1"), posting("1")])
    assert result.inserted == 1
    assert query(migrated_db, "SELECT count(*) FROM jobs") == [(1,)]


async def test_failure_midway_rolls_back_the_whole_board(migrated_db, board):
    with pytest.raises(psycopg.DataError):
        await store(
            migrated_db, board, [posting("1"), posting("2", title="Bad\x00Title")]
        )
    assert query(migrated_db, "SELECT count(*) FROM jobs") == [(0,)]
    assert query(migrated_db, "SELECT count(*) FROM job_sources") == [(0,)]


async def test_inline_descriptions_are_stored_for_internships_only(migrated_db, board):
    result = await store(
        migrated_db,
        board,
        [
            posting("1", description="Optimize routes with integer programming."),
            posting("2", title="Senior Engineer", description="Lead a team."),
        ],
    )
    assert (result.inline_descriptions, result.needs_description) == (1, [])
    assert query(
        migrated_db, "SELECT posting_key, description FROM jobs ORDER BY id"
    ) == [
        ("greenhouse:1", "Optimize routes with integer programming."),
        ("greenhouse:2", None),
    ]
    assert query(
        migrated_db,
        "SELECT c.slug FROM job_categories jc JOIN categories c ON c.id = jc.category_id ORDER BY 1",
    ) == [("operations_research",), ("software",)]


async def test_edited_inline_description_counts_as_a_change(migrated_db, board):
    await store(migrated_db, board, [posting("1", description="v1")])
    unchanged = await store(migrated_db, board, [posting("1", description="v1")])
    edited = await store(migrated_db, board, [posting("1", description="v2")])
    assert (unchanged.unchanged, unchanged.inline_descriptions) == (1, 0)
    assert (edited.updated, edited.inline_descriptions) == (1, 1)
    assert query(migrated_db, "SELECT description FROM jobs") == [("v2",)]
