from datetime import datetime

import httpx
from pydantic import BaseModel, ValidationError

from devradar.ingestion.classify import is_remote
from devradar.ingestion.countries import country_code
from devradar.ingestion.models import Board, BoardSnapshot, MalformedResponse, Posting
from devradar.ingestion.parsing import (
    NonEmptyStr,
    collapse_whitespace,
    get_board_response,
    html_to_text,
    read_json,
)

API_BASE = "https://api.ashbyhq.com/posting-api/job-board"
DESCRIPTION_KEYS = {"descriptionHtml", "descriptionPlain"}


class _PostalAddress(BaseModel):
    addressCountry: str | None = None


class _Address(BaseModel):
    postalAddress: _PostalAddress | None = None

    @property
    def country(self) -> str | None:
        name = self.postalAddress.addressCountry if self.postalAddress else None
        return country_code(name) if name else None


class _SecondaryLocation(BaseModel):
    location: str = ""
    address: _Address | None = None


class _Job(BaseModel):
    id: NonEmptyStr
    title: NonEmptyStr
    jobUrl: NonEmptyStr
    location: str | None = None
    address: _Address | None = None
    secondaryLocations: list[_SecondaryLocation] = []
    publishedAt: datetime | None = None
    employmentType: str | None = None
    isRemote: bool | None = None
    isListed: bool | None = None
    descriptionPlain: str | None = None
    descriptionHtml: str | None = None


class AshbyAdapter:
    ats = "ashby"

    def parse(self, raw: dict) -> Posting:
        return parse_posting(raw)

    async def fetch_postings(
        self, client: httpx.AsyncClient, board: Board
    ) -> BoardSnapshot:
        response = await get_board_response(client, f"{API_BASE}/{board.token}", board)
        payload = read_json(response)
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            raise MalformedResponse(f"ashby:{board.token} returned no jobs list")

        postings, rejected = [], 0
        for raw in jobs:
            try:
                posting = parse_posting(raw)
            except ValidationError:
                rejected += 1
                continue
            if posting.raw.get("isListed") is not False:
                postings.append(posting)
        return BoardSnapshot(postings, rejected, response.headers.get("etag"))

    async def fetch_description(
        self, client: httpx.AsyncClient, board: Board, external_id: str
    ) -> str:
        raise NotImplementedError("Ashby descriptions arrive with each posting")


def parse_posting(raw: dict) -> Posting:
    job = _Job.model_validate(raw)
    locations = [job.location or ""] + [
        entry.location for entry in job.secondaryLocations
    ]
    locations = [location.strip() for location in locations if location.strip()]
    if job.isRemote and not any(is_remote(location) for location in locations):
        locations.append("Remote")

    hints = {
        (location or "").strip(): address.country
        for location, address in [(job.location, job.address)]
        + [(entry.location, entry.address) for entry in job.secondaryLocations]
        if address and address.country
    }

    if job.descriptionPlain:
        description = collapse_whitespace(job.descriptionPlain)
    else:
        description = html_to_text(job.descriptionHtml or "")

    return Posting(
        source="ashby",
        external_id=job.id,
        title=job.title,
        url=job.jobUrl,
        locations=tuple(dict.fromkeys(locations)),
        posted_at=job.publishedAt,
        employment_type=job.employmentType,
        raw={key: value for key, value in raw.items() if key not in DESCRIPTION_KEYS},
        description=description,
        country_hints=hints,
    )
