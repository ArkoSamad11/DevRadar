import httpx
import pytest
import respx
from pydantic import ValidationError

from devradar.ingestion.ashby import API_BASE, AshbyAdapter, parse_posting
from devradar.ingestion.models import Board, MalformedResponse

BOARD = Board(id=1, company_id=1, ats="ashby", token="circleback")


def raw_job(**overrides):
    job = {
        "id": "07b5ee4f-03d5-4cf1-9506-55faac19cdaf",
        "title": "Software Engineering Intern",
        "employmentType": "Intern",
        "location": "San Francisco",
        "secondaryLocations": [{"location": "New York"}, {"location": "San Francisco"}],
        "publishedAt": "2026-04-01T17:49:45.686+00:00",
        "isListed": True,
        "isRemote": False,
        "jobUrl": "https://jobs.ashbyhq.com/circleback/07b5ee4f-03d5-4cf1-9506-55faac19cdaf",
        "descriptionHtml": "<p>Build the <b>source of truth</b></p>",
        "descriptionPlain": "Build the\n\nsource of truth",
    }
    return job | overrides


def test_parse_posting_normalizes_fields():
    posting = parse_posting(raw_job())
    assert posting.posting_key == "ashby:07b5ee4f-03d5-4cf1-9506-55faac19cdaf"
    assert posting.locations == ("San Francisco", "New York")
    assert posting.employment_type == "Intern"
    assert posting.description == "Build the source of truth"
    assert posting.posted_at.year == 2026


def test_description_text_is_kept_out_of_raw_but_in_the_hash():
    posting = parse_posting(raw_job())
    assert (
        "descriptionHtml" not in posting.raw and "descriptionPlain" not in posting.raw
    )
    edited = parse_posting(raw_job(descriptionPlain="Something else"))
    assert edited.content_hash != posting.content_hash


def test_html_description_is_used_when_plain_text_is_missing():
    posting = parse_posting(raw_job(descriptionPlain=None))
    assert posting.description == "Build the source of truth"


def test_remote_flag_adds_a_remote_location():
    posting = parse_posting(
        raw_job(isRemote=True, location="United States", secondaryLocations=[])
    )
    assert posting.locations == ("United States", "Remote")


def test_null_flags_are_accepted():
    posting = parse_posting(raw_job(isRemote=None, isListed=None))
    assert posting.locations == ("San Francisco", "New York")


@pytest.mark.parametrize("overrides", [{"id": ""}, {"title": None}, {"jobUrl": None}])
def test_parse_posting_rejects_invalid_records(overrides):
    with pytest.raises(ValidationError):
        parse_posting(raw_job(**overrides))


@pytest.mark.anyio
@respx.mock
async def test_fetch_postings_skips_unlisted_and_counts_rejected():
    respx.get(f"{API_BASE}/circleback").respond(
        json={
            "apiVersion": "1",
            "jobs": [
                raw_job(),
                raw_job(id="hidden", isListed=False),
                raw_job(title=""),
            ],
        }
    )
    async with httpx.AsyncClient() as client:
        postings, rejected = await AshbyAdapter().fetch_postings(client, BOARD)
    assert ([p.external_id for p in postings], rejected) == (
        ["07b5ee4f-03d5-4cf1-9506-55faac19cdaf"],
        1,
    )


@pytest.mark.anyio
@respx.mock
async def test_fetch_postings_raises_on_malformed_response():
    respx.get(f"{API_BASE}/circleback").respond(json={"error": "not found"})
    async with httpx.AsyncClient() as client:
        with pytest.raises(MalformedResponse):
            await AshbyAdapter().fetch_postings(client, BOARD)
