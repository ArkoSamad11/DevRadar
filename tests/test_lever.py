from datetime import UTC, datetime

import httpx
import pytest
import respx
from pydantic import ValidationError

from devradar.ingestion.lever import LeverAdapter, parse_posting
from devradar.ingestion.models import Board, MalformedResponse


def raw_posting(**overrides):
    posting = {
        "id": "a289405d-f147-412d-993e-b9436713bd35",
        "text": "Software Engineer Intern",
        "hostedUrl": "https://jobs.lever.co/drivemode/a289405d-f147-412d-993e-b9436713bd35",
        "categories": {
            "commitment": "Intern",
            "location": "Mountain View, CA",
            "allLocations": ["Mountain View, CA", "Tokyo"],
        },
        "createdAt": 1788579300611,
        "workplaceType": "hybrid",
        "descriptionPlain": "About Drivemode:\nWe build\napps.",
        "lists": [
            {
                "text": "Responsibilities:",
                "content": "<li>Build tools</li><li>Ship</li>",
            }
        ],
        "additionalPlain": "EEOC Statement",
        "salaryRange": {"min": 40, "max": 50, "currency": "USD"},
    }
    return posting | overrides


def test_parse_posting_normalizes_fields():
    posting = parse_posting(raw_posting())
    assert posting.posting_key == "lever:a289405d-f147-412d-993e-b9436713bd35"
    assert posting.title == "Software Engineer Intern"
    assert posting.locations == ("Mountain View, CA", "Tokyo")
    assert posting.employment_type == "Intern"
    assert posting.posted_at == datetime.fromtimestamp(1788579300.611, UTC)
    assert (
        posting.description
        == "About Drivemode: We build apps. Responsibilities: Build tools Ship"
    )


def test_description_sections_are_stripped_from_raw():
    posting = parse_posting(raw_posting())
    assert set(posting.raw) == {
        "id",
        "text",
        "hostedUrl",
        "categories",
        "createdAt",
        "workplaceType",
        "salaryRange",
    }


def test_null_and_missing_optionals_are_tolerated():
    posting = parse_posting(
        raw_posting(categories=None, createdAt=None, lists=None, descriptionPlain=None)
    )
    assert (posting.locations, posting.posted_at, posting.description) == ((), None, "")


def test_remote_workplace_adds_a_remote_location():
    posting = parse_posting(
        raw_posting(workplaceType="remote", categories={"location": "United States"})
    )
    assert posting.locations == ("United States", "Remote")


@pytest.mark.parametrize(
    "overrides", [{"id": None}, {"text": "  "}, {"hostedUrl": None}]
)
def test_parse_posting_rejects_invalid_records(overrides):
    with pytest.raises(ValidationError):
        parse_posting(raw_posting(**overrides))


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    ("region", "host"), [("global", "api.lever.co"), ("eu", "api.eu.lever.co")]
)
async def test_fetch_postings_uses_the_board_region(region, host):
    route = respx.get(f"https://{host}/v0/postings/acme").respond(
        json=[raw_posting(), raw_posting(id="")]
    )
    board = Board(id=1, company_id=1, ats="lever", token="acme", api_region=region)
    async with httpx.AsyncClient() as client:
        postings, rejected = await LeverAdapter().fetch_postings(client, board)
    assert (len(postings), rejected) == (1, 1)
    assert route.calls.last.request.url.params["mode"] == "json"


@pytest.mark.anyio
@respx.mock
async def test_fetch_postings_raises_on_error_object():
    respx.get("https://api.lever.co/v0/postings/acme").respond(
        json={"ok": False, "error": "Document not found"}
    )
    board = Board(id=1, company_id=1, ats="lever", token="acme")
    async with httpx.AsyncClient() as client:
        with pytest.raises(MalformedResponse):
            await LeverAdapter().fetch_postings(client, board)
