import asyncio
from dataclasses import dataclass

import httpx
import psycopg

from devradar.config import settings
from devradar.discovery.boards import BoardRef, company_slug, parse_board
from devradar.discovery.links import resolve_links
from devradar.discovery.lists import LIST_SOURCES, ListSource, fetch_listings

USER_AGENT = "DevRadar/0.1 (+https://github.com/ArkoSamad11/DevRadar)"


@dataclass
class DiscoveryStats:
    source: str
    listings: int = 0
    unresolved_links: int = 0
    without_board: int = 0
    boards_seen: int = 0
    new_boards: int = 0
    new_companies: int = 0

    def __str__(self) -> str:
        return (
            f"{self.source}: {self.listings} listings -> {self.boards_seen} boards "
            f"({self.new_boards} new, {self.new_companies} new companies); "
            f"{self.without_board} without a pollable board, "
            f"{self.unresolved_links} unresolved links"
        )


async def discover(
    conn: psycopg.AsyncConnection,
    client: httpx.AsyncClient,
    sources: tuple[ListSource, ...] = LIST_SOURCES,
) -> list[DiscoveryStats]:
    return [await discover_source(conn, client, source) for source in sources]


async def discover_source(
    conn: psycopg.AsyncConnection, client: httpx.AsyncClient, source: ListSource
) -> DiscoveryStats:
    stats = DiscoveryStats(source.name)
    listings = await fetch_listings(client, source)
    stats.listings = len(listings)

    resolved = {}
    if source.shortened_links:
        resolved = await resolve_links(
            conn, client, [listing.url for listing in listings]
        )

    boards: dict[BoardRef, str] = {}
    for listing in listings:
        url = resolved.get(listing.url) if source.shortened_links else listing.url
        if url is None:
            stats.unresolved_links += 1
            continue
        ref = parse_board(url)
        if ref is None:
            stats.without_board += 1
            continue
        boards.setdefault(ref, listing.company_name)
    stats.boards_seen = len(boards)

    async with conn.transaction():
        for ref, company_name in boards.items():
            new_company, new_board = await upsert_board(
                conn, ref, company_name, source.name
            )
            stats.new_companies += new_company
            stats.new_boards += new_board
    return stats


async def upsert_board(
    conn: psycopg.AsyncConnection, ref: BoardRef, company_name: str, discovered_via: str
) -> tuple[bool, bool]:
    cursor = await conn.execute(
        "SELECT 1 FROM job_boards WHERE ats = %s AND api_region = %s AND board_token = %s",
        (ref.ats, ref.api_region, ref.token),
    )
    if await cursor.fetchone():
        return False, False

    cursor = await conn.execute(
        """
        INSERT INTO companies (name, slug) VALUES (%s, %s)
        ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug
        RETURNING id, xmax = 0
        """,
        (company_name, company_slug(company_name)),
    )
    company_id, new_company = await cursor.fetchone()

    cursor = await conn.execute(
        """
        INSERT INTO job_boards (company_id, ats, api_region, board_token, discovered_via)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        (company_id, ref.ats, ref.api_region, ref.token, discovered_via),
    )
    return new_company, await cursor.fetchone() is not None


async def run() -> list[DiscoveryStats]:
    async with (
        await psycopg.AsyncConnection.connect(
            settings.database_url, autocommit=True
        ) as conn,
        httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client,
    ):
        return await discover(conn, client)


def main() -> None:
    for stats in asyncio.run(run()):
        print(stats)
