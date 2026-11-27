import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from psycopg import sql
from psycopg.rows import dict_row

from devradar.api.jobs import SUMMARY_COLUMNS, summary_from_row
from devradar.api.pagination import contains_pattern
from devradar.api.schemas import (
    Category,
    JobMatch,
    MatchKind,
    MatchResponse,
    RoleScore,
)
from devradar.matching.eligibility import Degree, mentions_work_authorization
from devradar.matching.index import CONFIDENT_ROLE_SCORE, IndexCache, Match
from devradar.matching.limits import RateLimiter
from devradar.matching.pdf import MAX_CHARACTERS, UnreadablePdf, extract_text
from devradar.matching.skills import find_skills
from devradar.roles.model import RoleModel, load_model

MAX_UPLOAD_BYTES = 1_000_000
MIN_WORDS = 30
MAX_CONCURRENT_PARSES = 2
MAX_MISSING_SKILLS = 8

router = APIRouter(tags=["match"])


class Matcher:
    """Everything POST /match keeps between requests; resumes are never part of it."""

    def __init__(self, pool) -> None:
        self.model = load_model()
        self.indexes = IndexCache(pool, self.model)
        self.limiter = RateLimiter(per_client=10, total=60, window=60)
        self.parsing = asyncio.Semaphore(MAX_CONCURRENT_PARSES)


@router.post(
    "/match",
    response_model=MatchResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "text/plain": {"schema": {"type": "string"}},
            },
        }
    },
)
async def match_resume(
    request: Request,
    kind: MatchKind = MatchKind.internships,
    degree: Degree | None = None,
    term: Annotated[
        str | None, Query(pattern=r"^(spring|summer|fall|winter) 20\d\d$")
    ] = None,
    graduation: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    category: Annotated[list[Category] | None, Query()] = None,
    location: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    remote: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> MatchResponse:
    """Rank postings against a resume, sent as a PDF or plain text body.

    The resume is read in memory to answer this request and then discarded.
    Postings are left out when they clearly state a degree, term or graduation year that the
    applicant does not meet. Full-time postings are compared by title only, without senior titles.
    """
    matcher = request.app.state.matcher
    if not matcher.limiter.allow(client_address(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many matches, try later"
        )
    resume = await read_resume(request, matcher)
    if len(resume.split()) < MIN_WORDS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"fewer than {MIN_WORDS} words found; scanned PDFs have no readable text",
        )

    internships = kind is MatchKind.internships
    index = await matcher.indexes.get(internships)
    async with request.app.state.pool.connection() as conn:
        allowed = await ids_passing_filters(
            conn, internships, category, location, remote
        )
    matches = await asyncio.to_thread(
        index.rank,
        matcher.model,
        resume,
        limit,
        degree=degree,
        term=term,
        graduation_year=graduation,
        allowed_ids=allowed,
    )
    skills = find_skills(resume)
    async with request.app.state.pool.connection() as conn:
        details = await describe_matches(conn, matches, skills)
    roles = score_roles(matcher.model, resume)
    return MatchResponse(
        roles=roles,
        confident=roles[0].score >= CONFIDENT_ROLE_SCORE,
        skills=sorted(skills),
        matches=details,
    )


def client_address(request: Request) -> str:
    # Railway's proxy sets X-Real-IP; without it every visitor would share one limit.
    direct = request.client.host if request.client else "unknown"
    return request.headers.get("x-real-ip", direct)


async def read_resume(request: Request, matcher: Matcher) -> str:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ("application/pdf", "text/plain"):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "send application/pdf or text/plain"
        )
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "the limit is 1 MB")
    if content_type == "text/plain":
        return body.decode("utf-8", errors="replace")[:MAX_CHARACTERS]
    if not body.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "not a PDF file")
    if matcher.parsing.locked():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "busy, try again shortly"
        )
    async with matcher.parsing:
        try:
            return await extract_text(bytes(body))
        except UnreadablePdf as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
            ) from error


def score_roles(model: RoleModel, resume: str) -> list[RoleScore]:
    scores = sorted(model.scores(resume).items(), key=lambda item: -item[1])
    return [
        RoleScore(
            role=role,
            score=round(score, 3),
            evidence=[term for term, _ in model.explain(resume, role)]
            if score >= CONFIDENT_ROLE_SCORE
            else [],
        )
        for role, score in scores
    ]


async def ids_passing_filters(
    conn, internships: bool, category, location: str | None, remote: bool | None
) -> set[int] | None:
    conditions, params = [], {"internships": internships}
    if category:
        params["categories"] = [c.value for c in category]
        conditions.append(
            sql.SQL("""
                EXISTS (
                    SELECT 1 FROM job_categories jc
                    JOIN categories cat ON cat.id = jc.category_id
                    WHERE jc.job_id = j.id AND cat.slug = ANY(%(categories)s)
                )
            """)
        )
    if location:
        params["location"] = contains_pattern(location)
        conditions.append(
            sql.SQL("""
                EXISTS (
                    SELECT 1 FROM job_locations l
                    WHERE l.job_id = j.id AND l.raw ILIKE %(location)s
                )
            """)
        )
    if remote is not None:
        params["remote"] = remote
        conditions.append(
            sql.SQL("""
                EXISTS (
                    SELECT 1 FROM job_locations l WHERE l.job_id = j.id AND l.is_remote
                ) = %(remote)s
            """)
        )
    if not conditions:
        return None
    query = sql.SQL("""
        SELECT j.id FROM jobs j
        WHERE j.status = 'active' AND j.is_internship = %(internships)s AND {filters}
    """).format(filters=sql.SQL(" AND ").join(conditions))
    cursor = await conn.execute(query, params)
    return {row[0] for row in await cursor.fetchall()}


async def describe_matches(
    conn, matches: list[Match], skills: set[str]
) -> list[JobMatch]:
    query = sql.SQL("""
        SELECT {columns}, coalesce(j.description, '') AS description
        FROM jobs j JOIN companies c ON c.id = j.company_id
        WHERE j.id = ANY(%(ids)s) AND j.status = 'active'
    """).format(columns=SUMMARY_COLUMNS)
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(query, {"ids": [m.job_id for m in matches]})
        rows = {row["id"]: row for row in await cursor.fetchall()}

    described = []
    for match in matches:
        # A posting can close between the index being built and this query.
        if (row := rows.get(match.job_id)) is None:
            continue
        wanted = find_skills(f"{row['title']}\n{row['description']}")
        described.append(
            JobMatch(
                job=summary_from_row(row),
                score=match.score,
                similarity=match.similarity,
                role_fit=match.role_fit,
                matched_terms=match.matched_terms,
                matched_skills=sorted(wanted & skills),
                missing_skills=sorted(wanted - skills)[:MAX_MISSING_SKILLS],
                mentions_work_authorization=mentions_work_authorization(
                    row["description"]
                ),
                similar_postings=match.duplicate_ids,
                degrees=[d for d in Degree if d in match.requirements.degrees],
                graduation=match.requirements.graduation,
            )
        )
    return described
