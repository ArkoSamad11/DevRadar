from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from pydantic import ValidationError

from devradar.ingestion.greenhouse import (
    API_BASE,
    GreenhouseAdapter,
    html_to_text,
    parse_posting,
)
from devradar.ingestion.models import Board, MalformedResponse

BOARD = Board(id=1, company_id=1, ats="greenhouse", token="neuralink")


def raw_job(**overrides):
    job = {
        "id": 7565469003,
        "title": "Software Engineer Intern, Implant ",
        "absolute_url": "https://boards.greenhouse.io/neuralink/jobs/7565469003",
        "location": {"name": "Austin, Texas, United States; Fremont, CA; "},
        "first_published": "2026-02-18T17:29:03-05:00",
        "updated_at": "2026-09-30T18:24:09-04:00",
        "metadata": [
            {"name": "Employment Type", "value": "Internship_non-exempt"},
            {"name": "Team", "value": ["Implant"]},
        ],
    }
    return job | overrides


def test_parse_posting_normalizes_fields():
    posting = parse_posting(raw_job())
    assert posting.posting_key == "greenhouse:7565469003"
    assert posting.title == "Software Engineer Intern, Implant"
    assert posting.locations == ("Austin, Texas, United States", "Fremont, CA")
    assert posting.posted_at == datetime(
        2026, 2, 18, 17, 29, 3, tzinfo=timezone(-timedelta(hours=5))
    )
    assert posting.employment_type == "Internship_non-exempt"


def test_parse_posting_tolerates_missing_optional_fields():
    posting = parse_posting(raw_job(location=None, first_published=None, metadata=None))
    assert (posting.locations, posting.posted_at, posting.employment_type) == (
        (),
        None,
        None,
    )


@pytest.mark.parametrize(
    "overrides",
    [{"id": "not-a-number"}, {"title": "   "}, {"title": None}, {"absolute_url": None}],
)
def test_parse_posting_rejects_invalid_records(overrides):
    with pytest.raises(ValidationError):
        parse_posting(raw_job(**overrides))


def test_content_hash_changes_when_posting_is_edited():
    original = parse_posting(raw_job())
    assert parse_posting(raw_job()).content_hash == original.content_hash
    assert (
        parse_posting(raw_job(updated_at="2026-10-01T00:00:00Z")).content_hash
        != original.content_hash
    )


def test_ignored_keys_are_not_stored_or_hashed():
    posting = parse_posting(raw_job(data_compliance=[{"type": "gdpr"}]))
    assert "data_compliance" not in posting.raw
    assert (
        posting.content_hash == parse_posting(raw_job(data_compliance=[])).content_hash
    )


def test_html_to_text_decodes_double_escaped_html():
    content = "&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;ul&gt;&lt;li&gt;C++&lt;/li&gt;&lt;/ul&gt;"
    assert html_to_text(content) == "Build & ship C++"


@pytest.mark.anyio
@respx.mock
async def test_fetch_postings_counts_rejected_records():
    respx.get(f"{API_BASE}/neuralink/jobs").respond(
        json={"jobs": [raw_job(), raw_job(id=2, title=""), {"unexpected": True}]}
    )
    async with httpx.AsyncClient() as client:
        postings, rejected = await GreenhouseAdapter().fetch_postings(client, BOARD)
    assert ([p.external_id for p in postings], rejected) == (["7565469003"], 2)


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, json={"error": "nope"}), httpx.Response(200, text="<html>")],
)
async def test_fetch_postings_raises_on_malformed_board_response(response):
    respx.get(f"{API_BASE}/neuralink/jobs").mock(return_value=response)
    async with httpx.AsyncClient() as client:
        with pytest.raises(MalformedResponse):
            await GreenhouseAdapter().fetch_postings(client, BOARD)


@pytest.mark.anyio
@respx.mock
async def test_fetch_description_returns_plain_text():
    respx.get(f"{API_BASE}/neuralink/jobs/42").respond(
        json={"id": 42, "content": "&lt;p&gt;Write firmware&lt;/p&gt;"}
    )
    async with httpx.AsyncClient() as client:
        assert (
            await GreenhouseAdapter().fetch_description(client, BOARD, "42")
            == "Write firmware"
        )
