from typing import Annotated

from fastapi import APIRouter, Query
from psycopg import sql
from psycopg.rows import dict_row

from devradar.api.deps import Connection
from devradar.api.pagination import contains_pattern, decode_cursor, encode_cursor
from devradar.api.schemas import CompanyPage, CompanySummary

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=CompanyPage)
async def list_companies(
    conn: Connection,
    q: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    hiring_interns: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> CompanyPage:
    conditions = [sql.SQL("TRUE")]
    params: dict[str, object] = {"fetch": limit + 1}
    if q:
        params["pattern"] = contains_pattern(q)
        conditions.append(sql.SQL("c.name ILIKE %(pattern)s"))
    if hiring_interns:
        conditions.append(
            sql.SQL("""
                EXISTS (
                    SELECT 1 FROM jobs j
                    WHERE j.company_id = c.id AND j.status = 'active' AND j.is_internship
                )
            """)
        )
    if cursor:
        (params["after_slug"],) = decode_cursor(cursor, str)
        conditions.append(sql.SQL("c.slug > %(after_slug)s"))

    query = sql.SQL("""
        WITH page AS (
            SELECT c.id FROM companies c
            WHERE {where}
            ORDER BY c.slug
            LIMIT %(fetch)s
        )
        SELECT c.name, c.slug,
               (SELECT count(*) FROM jobs j
                WHERE j.company_id = c.id AND j.status = 'active') AS active_jobs,
               (SELECT count(*) FROM jobs j
                WHERE j.company_id = c.id AND j.status = 'active' AND j.is_internship)
                   AS active_internships,
               ARRAY(
                   SELECT json_build_object('ats', b.ats, 'token', b.board_token)
                   FROM job_boards b WHERE b.company_id = c.id ORDER BY b.ats, b.board_token
               ) AS boards
        FROM page p JOIN companies c ON c.id = p.id
        ORDER BY c.slug
    """).format(where=sql.SQL(" AND ").join(conditions))

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        rows = await cur.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    return CompanyPage(
        items=[CompanySummary(**row) for row in rows],
        limit=limit,
        next_cursor=encode_cursor(rows[-1]["slug"]) if has_more else None,
    )
