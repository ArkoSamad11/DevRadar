from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ValidationError

from devradar.ingestion.classify import is_remote
from devradar.ingestion.models import Board, MalformedResponse, Posting
from devradar.ingestion.parsing import (
    NonEmptyStr,
    collapse_whitespace,
    html_to_text,
    read_json,
)

API_HOSTS = {"global": "https://api.lever.co", "eu": "https://api.eu.lever.co"}
DESCRIPTION_KEYS = {
    "description",
    "descriptionPlain",
    "descriptionBody",
    "descriptionBodyPlain",
    "opening",
    "openingPlain",
    "lists",
    "additional",
    "additionalPlain",
    "salaryDescription",
    "salaryDescriptionPlain",
}


class _Categories(BaseModel):
    location: str | None = None
    allLocations: list[str] | None = None
    commitment: str | None = None


class _ListSection(BaseModel):
    text: str | None = None
    content: str | None = None


class _Posting(BaseModel):
    id: NonEmptyStr
    text: NonEmptyStr
    hostedUrl: NonEmptyStr
    categories: _Categories | None = None
    createdAt: int | None = None
    workplaceType: str | None = None
    descriptionPlain: str | None = None
    lists: list[_ListSection] | None = None


class LeverAdapter:
    ats = "lever"

    def parse(self, raw: dict) -> Posting:
        return parse_posting(raw)

    async def fetch_postings(
        self, client: httpx.AsyncClient, board: Board
    ) -> tuple[list[Posting], int]:
        host = API_HOSTS[board.api_region]
        response = await client.get(
            f"{host}/v0/postings/{board.token}", params={"mode": "json"}
        )
        response.raise_for_status()
        payload = read_json(response)
        if not isinstance(payload, list):
            raise MalformedResponse(f"lever:{board.token} returned no postings list")

        postings, rejected = [], 0
        for raw in payload:
            try:
                postings.append(parse_posting(raw))
            except ValidationError:
                rejected += 1
        return postings, rejected

    async def fetch_description(
        self, client: httpx.AsyncClient, board: Board, external_id: str
    ) -> str:
        raise NotImplementedError("Lever descriptions arrive with each posting")


def parse_posting(raw: dict) -> Posting:
    posting = _Posting.model_validate(raw)
    categories = posting.categories or _Categories()
    locations = categories.allLocations or [categories.location or ""]
    locations = [location.strip() for location in locations if location.strip()]
    if posting.workplaceType == "remote" and not any(
        is_remote(loc) for loc in locations
    ):
        locations.append("Remote")

    sections = [collapse_whitespace(posting.descriptionPlain or "")]
    for section in posting.lists or []:
        sections.append(
            f"{section.text or ''} {html_to_text(section.content or '')}".strip()
        )

    return Posting(
        source="lever",
        external_id=posting.id,
        title=posting.text,
        url=posting.hostedUrl,
        locations=tuple(dict.fromkeys(locations)),
        posted_at=(
            datetime.fromtimestamp(posting.createdAt / 1000, UTC)
            if posting.createdAt
            else None
        ),
        employment_type=categories.commitment,
        raw={key: value for key, value in raw.items() if key not in DESCRIPTION_KEYS},
        description=" ".join(section for section in sections if section),
    )
