import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx


class MalformedResponse(Exception):
    pass


class NotModified(Exception):
    pass


@dataclass(frozen=True)
class Board:
    id: int
    company_id: int
    ats: str
    token: str
    api_region: str = "global"
    etag: str | None = None


@dataclass(frozen=True)
class Posting:
    source: str
    external_id: str
    title: str
    url: str
    locations: tuple[str, ...]
    posted_at: datetime | None
    employment_type: str | None
    raw: dict = field(compare=False, hash=False)
    description: str | None = None
    # Location -> ISO country code, where the ATS reports one.
    country_hints: dict[str, str] = field(
        default_factory=dict, compare=False, hash=False
    )

    @property
    def posting_key(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def content_hash(self) -> str:
        content = json.dumps(self.raw, sort_keys=True)
        if self.description is not None:
            content += "\0" + self.description
        return hashlib.sha256(content.encode()).hexdigest()


@dataclass(frozen=True)
class BoardSnapshot:
    postings: list[Posting]
    rejected: int
    etag: str | None


class Adapter(Protocol):
    ats: str

    def parse(self, raw: dict) -> Posting: ...

    async def fetch_postings(
        self, client: httpx.AsyncClient, board: Board
    ) -> BoardSnapshot: ...

    async def fetch_description(
        self, client: httpx.AsyncClient, board: Board, external_id: str
    ) -> str: ...
