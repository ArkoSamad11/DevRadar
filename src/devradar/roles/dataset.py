import asyncio
import gzip
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import psycopg

from devradar.config import settings
from devradar.discovery.run import USER_AGENT
from devradar.ingestion.classify import categorize, is_internship
from devradar.ingestion.models import Board, MalformedResponse
from devradar.ingestion.pacing import Pacer
from devradar.ingestion.worker import ADAPTERS, ATS_MIN_INTERVAL
from devradar.roles.text import ROLES

CACHE = Path("data/role-examples.json.gz")
INLINE_DESCRIPTION_SOURCES = ("ashby", "lever")
MIN_DESCRIPTION_LENGTH = 300
# Reposts of one role differ only in their first lines, such as the city.
DUPLICATE_TAIL_LENGTH = 1500


@dataclass(frozen=True)
class Example:
    company_id: int
    title: str
    description: str
    source: str
    is_internship: bool

    @property
    def roles(self) -> set[str]:
        return categorize(self.title) & set(ROLES)


def stored_internships(conninfo: str) -> list[Example]:
    with psycopg.connect(conninfo) as conn:
        rows = conn.execute(
            """
            SELECT j.company_id, j.title, j.description, min(s.source)
            FROM jobs j JOIN job_sources s ON s.job_id = j.id
            WHERE j.is_internship AND j.description IS NOT NULL
            GROUP BY j.id ORDER BY j.id
            """
        ).fetchall()
    return [Example(*row, True) for row in rows]


async def fetch_full_time(conninfo: str) -> list[Example]:
    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        cursor = await conn.execute(
            """
            SELECT id, company_id, ats, board_token, api_region FROM job_boards
            WHERE enabled AND ats = ANY(%s) ORDER BY id
            """,
            (list(INLINE_DESCRIPTION_SOURCES),),
        )
        boards = [Board(*row) for row in await cursor.fetchall()]

    slots = {ats: asyncio.Semaphore(8) for ats in INLINE_DESCRIPTION_SOURCES}
    pacers = {
        ats: Pacer(ATS_MIN_INTERVAL.get(ats, 0.0)) for ats in INLINE_DESCRIPTION_SOURCES
    }
    examples: list[Example] = []

    async def fetch(client: httpx.AsyncClient, board: Board) -> None:
        async with slots[board.ats]:
            await pacers[board.ats].wait()
            try:
                snapshot = await ADAPTERS[board.ats].fetch_postings(client, board)
            except (httpx.HTTPError, MalformedResponse):
                return
        examples.extend(
            Example(board.company_id, p.title, p.description or "", p.source, False)
            for p in snapshot.postings
            if not is_internship(p.title, p.employment_type)
        )

    async with httpx.AsyncClient(
        timeout=40, headers={"User-Agent": USER_AGENT}
    ) as client:
        await asyncio.gather(*(fetch(client, board) for board in boards))
    return sorted(examples, key=lambda e: (e.company_id, e.title, e.description))


def deduplicate(examples: list[Example]) -> list[Example]:
    seen, unique = set(), []
    for example in examples:
        key = (example.company_id, example.description[-DUPLICATE_TAIL_LENGTH:])
        if len(example.description) >= MIN_DESCRIPTION_LENGTH and key not in seen:
            seen.add(key)
            unique.append(example)
    return unique


def load_examples(refresh: bool = False, cache: Path = CACHE) -> list[Example]:
    if refresh or not cache.exists():
        examples = stored_internships(settings.database_url)
        examples += asyncio.run(fetch_full_time(settings.database_url))
        cache.parent.mkdir(exist_ok=True)
        with gzip.open(cache, "wt") as f:
            json.dump([asdict(example) for example in examples], f)
    with gzip.open(cache, "rt") as f:
        return deduplicate([Example(**row) for row in json.load(f)])
