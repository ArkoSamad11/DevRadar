from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from devradar.matching.eligibility import Degree


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
    countries: list[str] = Field(
        description="ISO codes; empty if the locations don't say"
    )


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


class BoardRef(BaseModel):
    ats: str
    token: str


class CompanySummary(BaseModel):
    name: str
    slug: str
    active_jobs: int
    active_internships: int
    boards: list[BoardRef]


class CompanyPage(BaseModel):
    items: list[CompanySummary]
    limit: int
    next_cursor: str | None


class SourceStatus(BaseModel):
    ats: str
    supported: bool
    boards: int
    enabled_boards: int
    last_success_at: datetime | None
    active_jobs: int


class IngestionRun(BaseModel):
    id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    boards: int
    failed_boards: int
    postings_fetched: int
    inserted: int
    updated: int
    closed: int
    rejected: int
    descriptions_stored: int


class IngestionRunPage(BaseModel):
    items: list[IngestionRun]
    limit: int
    next_cursor: str | None


class RoleScore(BaseModel):
    role: str
    score: float
    evidence: list[str]


class MatchKind(StrEnum):
    internships = "internships"
    full_time = "full_time"


class GraduationRange(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    earliest: date | None
    latest: date | None


class JobMatch(BaseModel):
    job: JobSummary
    score: float
    similarity: float
    role_fit: float
    matched_terms: list[str]
    matched_skills: list[str]
    missing_skills: list[str]
    mentions_work_authorization: bool
    similar_postings: list[int]
    degrees: list[Degree] = Field(description="accepted degrees; empty if not stated")
    graduation: GraduationRange | None = Field(description="null if not stated")
    school: str | None = Field(
        description="the one school whose students may apply, if any"
    )


class MatchResponse(BaseModel):
    roles: list[RoleScore]
    confident: bool
    skills: list[str]
    matches: list[JobMatch]
