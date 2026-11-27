import asyncio
import gzip
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import TypeAdapter

from devradar.api.jobs import SUMMARY_COLUMNS, summary_from_row
from devradar.api.schemas import JobSummary
from devradar.matching.index import latest_run

WEB = Path(__file__).parent.parent / "web"
FRESH_SECONDS = 300
STALE_SECONDS = 86_400
PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:;"
        " connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-cache",
}
ASSETS = {
    "app.js": "text/javascript",
    "theme.js": "text/javascript",
    "styles.css": "text/css",
}

router = APIRouter()
_summaries = TypeAdapter(list[JobSummary])


class Snapshot:
    """Every active internship as compressed JSON, rebuilt after new ingestion runs."""

    def __init__(self) -> None:
        self.run_id = -1
        self.compressed = b""
        self._lock = asyncio.Lock()

    async def get(self, pool) -> tuple[int, bytes]:
        async with self._lock:
            run_id = await latest_run(pool)
            if run_id != self.run_id:
                self.compressed = gzip.compress(await self._read(pool), 6)
                self.run_id = run_id
            return self.run_id, self.compressed

    async def _read(self, pool) -> bytes:
        query = sql.SQL("""
            SELECT {columns} FROM jobs j JOIN companies c ON c.id = j.company_id
            WHERE j.status = 'active' AND j.is_internship
            ORDER BY j.listed_at DESC, j.id DESC
        """).format(columns=SUMMARY_COLUMNS)
        async with (
            pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(query)
            rows = await cursor.fetchall()
        return _summaries.dump_json([summary_from_row(row) for row in rows])


@router.get("/internships", response_model=list[JobSummary], tags=["jobs"])
async def all_internships(request: Request) -> Response:
    """Every active internship, newest first, in one cacheable response."""
    run_id, compressed = await request.app.state.snapshot.get(request.app.state.pool)
    etag = f'W/"run-{run_id}"'
    headers = {
        "ETag": etag,
        "Cache-Control": f"public, max-age={FRESH_SECONDS}, stale-while-revalidate={STALE_SECONDS}",
        "Vary": "Accept-Encoding",
    }
    if etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
        return Response(compressed, media_type="application/json", headers=headers)
    return Response(
        gzip.decompress(compressed), media_type="application/json", headers=headers
    )


@router.get("/", include_in_schema=False)
async def page() -> FileResponse:
    return FileResponse(
        WEB / "index.html", media_type="text/html", headers=PAGE_HEADERS
    )


def _asset_route(name: str, media_type: str):
    async def asset() -> FileResponse:
        return FileResponse(WEB / name, media_type=media_type, headers=PAGE_HEADERS)

    return asset


for _name, _media_type in ASSETS.items():
    router.add_api_route(
        f"/{_name}", _asset_route(_name, _media_type), include_in_schema=False
    )
