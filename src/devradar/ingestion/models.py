import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime


class MalformedResponse(Exception):
    pass


@dataclass(frozen=True)
class Board:
    id: int
    company_id: int
    ats: str
    token: str
    api_region: str = "global"


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

    @property
    def posting_key(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.raw, sort_keys=True).encode()).hexdigest()
