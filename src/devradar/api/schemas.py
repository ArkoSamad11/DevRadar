from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class Category(StrEnum):
    software = "software"
    ml_ai = "ml_ai"
    data_science = "data_science"
    systems = "systems"
    security = "security"
    quant = "quant"
    operations_research = "operations_research"
    math = "math"


class CompanyRef(BaseModel):
    name: str
    slug: str


class JobSummary(BaseModel):
    id: int
    title: str
    company: CompanyRef
    locations: list[str]
    remote: bool
    categories: list[str]
    is_internship: bool
    status: Literal["active", "closed"]
    listed_at: datetime
    url: str
    source: str


class JobPage(BaseModel):
    items: list[JobSummary]
    limit: int
    next_cursor: str | None


class JobSource(BaseModel):
    source: str
    external_id: str
    url: str


class JobDetail(JobSummary):
    description: str | None
    posted_at: datetime | None
    first_seen_at: datetime
    closed_at: datetime | None
    sources: list[JobSource]
