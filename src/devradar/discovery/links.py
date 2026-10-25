import asyncio
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
import psycopg

TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Resolution:
    short_url: str
    resolved_url: str | None
    status_code: int


async def resolve_links(
    conn: psycopg.AsyncConnection,
    client: httpx.AsyncClient,
    urls: list[str],
    concurrency: int = 8,
) -> dict[str, str | None]:
    cached = await _load_cached(conn, urls)
    pending = [url for url in dict.fromkeys(urls) if url not in cached]
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *(_resolve(client, semaphore, url) for url in pending)
    )
    fresh = [result for result in results if result is not None]
    await _store(conn, fresh)
    return cached | {result.short_url: result.resolved_url for result in fresh}


async def _resolve(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, url: str
) -> Resolution | None:
    async with semaphore:
        try:
            response = await client.get(url, follow_redirects=False)
        except httpx.HTTPError:
            return None
    if response.status_code in TRANSIENT_STATUSES:
        return None
    if response.is_redirect:
        location = urljoin(url, response.headers["location"])
        return Resolution(url, location, response.status_code)
    return Resolution(url, None, response.status_code)


async def _load_cached(
    conn: psycopg.AsyncConnection, urls: list[str]
) -> dict[str, str | None]:
    cursor = await conn.execute(
        "SELECT short_url, resolved_url FROM link_resolutions WHERE short_url = ANY(%s)",
        (urls,),
    )
    return dict(await cursor.fetchall())


async def _store(conn: psycopg.AsyncConnection, resolutions: list[Resolution]) -> None:
    async with conn.transaction(), conn.cursor() as cursor:
        await cursor.executemany(
            """
            INSERT INTO link_resolutions (short_url, resolved_url, status_code)
            VALUES (%s, %s, %s)
            ON CONFLICT (short_url) DO UPDATE
                SET resolved_url = EXCLUDED.resolved_url,
                    status_code = EXCLUDED.status_code,
                    resolved_at = now()
            """,
            [(r.short_url, r.resolved_url, r.status_code) for r in resolutions],
        )
