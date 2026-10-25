import html
from datetime import datetime
from typing import Annotated

import httpx
from pydantic import BaseModel, StringConstraints, ValidationError

from devradar.ingestion.models import Board, MalformedResponse, Posting
from devradar.ingestion.parsing import html_to_text, read_json

API_BASE = "https://boards-api.greenhouse.io/v1/boards"
IGNORED_KEYS = {"data_compliance"}

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Location(BaseModel):
    name: str = ""


class _Metadata(BaseModel):
    name: str
    value: object = None


class _Job(BaseModel):
    id: int
    title: NonEmptyStr
    absolute_url: NonEmptyStr
    location: _Location | None = None
    first_published: datetime | None = None
    metadata: list[_Metadata] | None = None


class GreenhouseAdapter:
    ats = "greenhouse"

    def parse(self, raw: dict) -> Posting:
        return parse_posting(raw)

    async def fetch_postings(
        self, client: httpx.AsyncClient, board: Board
    ) -> tuple[list[Posting], int]:
        response = await client.get(f"{API_BASE}/{board.token}/jobs")
        response.raise_for_status()
        jobs = _field(read_json(response), "jobs")
        if not isinstance(jobs, list):
            raise MalformedResponse(f"greenhouse:{board.token} returned no jobs list")

        postings, rejected = [], 0
        for raw in jobs:
            try:
                postings.append(parse_posting(raw))
            except ValidationError:
                rejected += 1
        return postings, rejected

    async def fetch_description(
        self, client: httpx.AsyncClient, board: Board, external_id: str
    ) -> str:
        response = await client.get(f"{API_BASE}/{board.token}/jobs/{external_id}")
        response.raise_for_status()
        # Greenhouse double-encodes descriptions: the HTML itself arrives entity-escaped.
        return html_to_text(html.unescape(_field(read_json(response), "content") or ""))


def parse_posting(raw: dict) -> Posting:
    job = _Job.model_validate(raw)
    location_name = job.location.name if job.location else ""
    return Posting(
        source="greenhouse",
        external_id=str(job.id),
        title=job.title,
        url=job.absolute_url,
        locations=tuple(
            part.strip() for part in location_name.split(";") if part.strip()
        ),
        posted_at=job.first_published,
        employment_type=_employment_type(job.metadata or []),
        raw={key: value for key, value in raw.items() if key not in IGNORED_KEYS},
    )


def _employment_type(metadata: list[_Metadata]) -> str | None:
    for field in metadata:
        if field.name.strip().lower() == "employment type" and isinstance(
            field.value, str
        ):
            return field.value
    return None


def _field(payload: object, key: str) -> object:
    return payload.get(key) if isinstance(payload, dict) else None
