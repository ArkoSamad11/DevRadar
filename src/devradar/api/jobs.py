from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from psycopg import sql
from psycopg.rows import dict_row

from devradar.api.deps import Connection
from devradar.api.pagination import (
    aware_datetime,
    contains_pattern,
    decode_cursor,
    encode_cursor,
)
from devradar.api.schemas import Category, CompanyRef, JobDetail, JobPage, JobSummary

router = APIRouter(prefix="/jobs", tags=["jobs"])

SUMMARY_COLUMNS = sql.SQL("""
    j.id, j.title, j.url, j.is_internship, j.status, j.listed_at,
    c.name AS company_name, c.slug AS company_slug,
    ARRAY(
        SELECT l.raw FROM job_locations l WHERE l.job_id = j.id ORDER BY l.raw
    ) AS locations,
    EXISTS (
        SELECT 1 FROM job_locations l WHERE l.job_id = j.id AND l.is_remote
    ) AS remote,
    ARRAY(
        SELECT cat.slug FROM job_categories jc
        JOIN categories cat ON cat.id = jc.category_id
        WHERE jc.job_id = j.id ORDER BY cat.slug
    ) AS categories,
    (SELECT s.source FROM job_sources s WHERE s.job_id = j.id ORDER BY s.id LIMIT 1) AS source
""")


@router.get("", response_model=JobPage)
async def list_jobs(
    conn: Connection,
    q: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    category: Annotated[list[Category] | None, Query()] = None,
    location: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    remote: bool | None = None,
    company: Annotated[str | None, Query(max_length=100)] = None,
    internships_only: bool = True,
    include_closed: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> JobPage:
    conditions = [sql.SQL("TRUE")]
    params: dict[str, object] = {"fetch": limit + 1}

    if internships_only:
        conditions.append(sql.SQL("j.is_internship"))
    if not include_closed:
        conditions.append(sql.SQL("j.status = 'active'"))
    if q:
        params["pattern"] = contains_pattern(q)
        conditions.append(sql.SQL("j.search_text LIKE lower(%(pattern)s)"))
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
        conditions.append(
            sql.SQL(
                "{negate} EXISTS (SELECT 1 FROM job_locations l"
                " WHERE l.job_id = j.id AND l.is_remote)"
            ).format(negate=sql.SQL("" if remote else "NOT"))
        )
    if company:
        params["company"] = company
        conditions.append(sql.SQL("c.slug = %(company)s"))
    if cursor:
        params["after_listed_at"], params["after_id"] = decode_cursor(
            cursor, aware_datetime, int
        )
        conditions.append(
            sql.SQL("(j.listed_at, j.id) < (%(after_listed_at)s, %(after_id)s)")
        )

    query = sql.SQL("""
        WITH page AS (
            SELECT j.id, j.listed_at FROM jobs j
            JOIN companies c ON c.id = j.company_id
            WHERE {where}
            ORDER BY j.listed_at DESC, j.id DESC
            LIMIT %(fetch)s
        )
        SELECT {columns}
        FROM page p
        JOIN jobs j ON j.id = p.id
        JOIN companies c ON c.id = j.company_id
        ORDER BY p.listed_at DESC, p.id DESC
    """).format(where=sql.SQL(" AND ").join(conditions), columns=SUMMARY_COLUMNS)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        rows = await cur.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        encode_cursor(rows[-1]["listed_at"], rows[-1]["id"]) if has_more else None
    )
    return JobPage(
        items=[_summary(row) for row in rows], limit=limit, next_cursor=next_cursor
    )


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(conn: Connection, job_id: int) -> JobDetail:
    query = sql.SQL("""
        SELECT {columns}, j.description, j.posted_at, j.first_seen_at, j.closed_at,
               ARRAY(
                   SELECT json_build_object(
                       'source', s.source, 'external_id', s.external_id, 'url', s.url
                   )
                   FROM job_sources s WHERE s.job_id = j.id ORDER BY s.id
               ) AS sources
        FROM jobs j JOIN companies c ON c.id = j.company_id
        WHERE j.id = %(job_id)s
    """).format(columns=SUMMARY_COLUMNS)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, {"job_id": job_id})
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return JobDetail(
        **_summary(row).model_dump(),
        description=row["description"],
        posted_at=row["posted_at"],
        first_seen_at=row["first_seen_at"],
        closed_at=row["closed_at"],
        sources=row["sources"],
    )


def _summary(row: dict) -> JobSummary:
    return JobSummary(
        id=row["id"],
        title=row["title"],
        company=CompanyRef(name=row["company_name"], slug=row["company_slug"]),
        locations=row["locations"],
        remote=row["remote"],
        categories=row["categories"],
        is_internship=row["is_internship"],
        status=row["status"],
        listed_at=row["listed_at"],
        url=row["url"],
        source=row["source"],
    )
