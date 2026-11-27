import argparse
import asyncio
import random
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
import psycopg
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from devradar.config import settings
from devradar.discovery.run import USER_AGENT
from devradar.ingestion.ashby import AshbyAdapter
from devradar.ingestion.countries import posting_countries
from devradar.ingestion.greenhouse import GreenhouseAdapter
from devradar.ingestion.lever import LeverAdapter
from devradar.ingestion.models import Adapter, Board, MalformedResponse, NotModified
from devradar.ingestion.pacing import Pacer
from devradar.ingestion.store import (
    DescriptionTask,
    StoreResult,
    close_board,
    confirm_misses,
    forget_content_hash,
    store_description,
    store_postings,
)

COUNTRY_BATCH = 1000

ADAPTERS: dict[str, Adapter] = {
    "greenhouse": GreenhouseAdapter(),
    "ashby": AshbyAdapter(),
    "lever": LeverAdapter(),
}
# Greenhouse answered 429 to bursts at 16 concurrent requests.
ATS_CONCURRENCY = {"greenhouse": 8}
# api.lever.co/robots.txt asks for "Crawl-delay: 1".
ATS_MIN_INTERVAL = {"lever": 1.0}
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
GONE_STATUSES = {404, 410}
DISABLE_AFTER_FAILURES = 5
RECHECK_DISABLED_AFTER = "1 day"
RUN_LOCK_ID = 7206312


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 20.0

    def delay(self, attempt: int, error: Exception) -> float:
        retry_after = _retry_after_seconds(error)
        if retry_after is not None:
            return min(self.max_delay, retry_after)
        return min(
            self.max_delay, self.base_delay * 2 ** (attempt - 1)
        ) * random.uniform(0.5, 1)


DEFAULT_RETRY = RetryPolicy()


@dataclass
class Stopwatch:
    seconds: float = 0.0


class RunInProgress(Exception):
    pass


class FetchFailed(Exception):
    def __init__(self, message: str, attempts: int, gone: bool) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.gone = gone


@dataclass
class BoardOutcome:
    board: Board
    status: str
    attempts: int
    fetched: int = 0
    rejected: int = 0
    store: StoreResult = field(default_factory=StoreResult)
    descriptions_fetched: int = 0
    descriptions_failed: int = 0
    duration_ms: int = 0
    error: str | None = None
    not_modified: bool = False
    etag: str | None = None
    gone: bool = False


@dataclass
class RunSummary:
    run_id: int
    status: str
    outcomes: list[BoardOutcome]
    duration_s: float


async def with_retries[T](
    operation: Callable[[], Awaitable[T]], policy: RetryPolicy
) -> tuple[T, int]:
    for attempt in range(1, policy.attempts + 1):
        try:
            return await operation(), attempt
        except (httpx.HTTPError, MalformedResponse) as error:
            if attempt == policy.attempts or not _is_retryable(error):
                raise FetchFailed(_describe(error), attempt, _is_gone(error)) from error
            await asyncio.sleep(policy.delay(attempt, error))
    raise AssertionError("unreachable")


class Ingestor:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        client: httpx.AsyncClient,
        http_concurrency: int = 16,
        retry: RetryPolicy = DEFAULT_RETRY,
    ) -> None:
        self.pool = pool
        self.client = client
        self.http_concurrency = http_concurrency
        self.http_slots: dict[str, asyncio.Semaphore] = {}
        self.pacers = {
            ats: Pacer(interval) for ats, interval in ATS_MIN_INTERVAL.items()
        }
        self.retry = retry

    async def run(self, run_id: int, boards: list[Board]) -> list[BoardOutcome]:
        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(self.ingest_board(run_id, board)) for board in boards
            ]
        return [task.result() for task in tasks]

    async def ingest_board(self, run_id: int, board: Board) -> BoardOutcome:
        active = Stopwatch()
        try:
            outcome = await self._ingest(board, active)
        except Exception as error:  # noqa: BLE001
            # A bug triggered by one board's data must not cancel the other boards' tasks.
            outcome = BoardOutcome(
                board, "failed", 1, error=f"{type(error).__name__}: {error}"
            )
        outcome.duration_ms = int(active.seconds * 1000)
        await self._record(run_id, outcome)
        return outcome

    async def _ingest(self, board: Board, active: Stopwatch) -> BoardOutcome:
        adapter = ADAPTERS[board.ats]
        try:
            snapshot, attempts = await with_retries(
                lambda: self._limited(
                    board.ats, adapter.fetch_postings(self.client, board), active
                ),
                self.retry,
            )
        except NotModified:
            outcome = BoardOutcome(board, "succeeded", 1, not_modified=True)
            async with self.pool.connection() as conn:
                outcome.store.closed = await confirm_misses(conn, board)
            return outcome
        except FetchFailed as failure:
            return BoardOutcome(
                board,
                "failed",
                failure.attempts,
                error=str(failure),
                gone=failure.gone,
            )

        outcome = BoardOutcome(
            board,
            "succeeded",
            attempts,
            len(snapshot.postings),
            snapshot.rejected,
            etag=snapshot.etag,
        )
        async with self.pool.connection() as conn:
            started = time.perf_counter()
            outcome.store = await store_postings(conn, board, snapshot.postings)
            active.seconds += time.perf_counter() - started
        fetched = await asyncio.gather(
            *(
                self._fetch_description(adapter, board, task)
                for task in outcome.store.needs_description
            )
        )
        outcome.descriptions_fetched = outcome.store.inline_descriptions + sum(fetched)
        outcome.descriptions_failed = len(fetched) - sum(fetched)
        return outcome

    async def _fetch_description(
        self, adapter, board: Board, task: DescriptionTask
    ) -> bool:
        try:
            description, _ = await with_retries(
                lambda: self._limited(
                    board.ats,
                    adapter.fetch_description(self.client, board, task.external_id),
                ),
                self.retry,
            )
        except FetchFailed:
            # Without this, an edited posting would look unchanged next run and keep old text.
            async with self.pool.connection() as conn:
                await forget_content_hash(conn, board.ats, task.external_id)
            return False
        async with self.pool.connection() as conn:
            await store_description(conn, task, description)
        return True

    async def _limited[T](
        self, ats: str, request: Awaitable[T], active: Stopwatch | None = None
    ) -> T:
        if ats not in self.http_slots:
            limit = ATS_CONCURRENCY.get(ats, self.http_concurrency)
            self.http_slots[ats] = asyncio.Semaphore(min(limit, self.http_concurrency))
        async with self.http_slots[ats]:
            if ats in self.pacers:
                await self.pacers[ats].wait()
            started = time.perf_counter()
            try:
                return await request
            finally:
                if active is not None:
                    active.seconds += time.perf_counter() - started

    async def _record(self, run_id: int, outcome: BoardOutcome) -> None:
        store = outcome.store
        try:
            async with self.pool.connection() as conn, conn.transaction():
                await _update_board(conn, outcome)
                await conn.execute(
                    """
                    INSERT INTO board_fetches (
                        run_id, job_board_id, status, attempts, fetched_count, inserted_count,
                        updated_count, unchanged_count, closed_count, reopened_count,
                        rejected_count, descriptions_fetched, descriptions_failed,
                        duration_ms, error, not_modified
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        outcome.board.id,
                        outcome.status,
                        outcome.attempts,
                        outcome.fetched,
                        store.inserted,
                        store.updated,
                        store.unchanged,
                        store.closed,
                        store.reopened,
                        outcome.rejected,
                        outcome.descriptions_fetched,
                        outcome.descriptions_failed,
                        outcome.duration_ms,
                        outcome.error,
                        outcome.not_modified,
                    ),
                )
        except psycopg.Error as error:
            print(
                f"could not record fetch for board {outcome.board.id}: {error}",
                file=sys.stderr,
            )


async def _update_board(conn: psycopg.AsyncConnection, outcome: BoardOutcome) -> None:
    if outcome.status == "succeeded":
        # A failed description fetch must not be hidden behind a 304 next run.
        save_etag = not outcome.not_modified and outcome.descriptions_failed == 0
        await conn.execute(
            """
            UPDATE job_boards
            SET consecutive_failures = 0,
                last_success_at = now(),
                enabled = true,
                disabled_at = NULL,
                etag = CASE WHEN %s THEN %s ELSE etag END
            WHERE id = %s
            """,
            (save_etag, outcome.etag, outcome.board.id),
        )
    elif outcome.gone:
        cursor = await conn.execute(
            """
            UPDATE job_boards
            SET consecutive_failures = consecutive_failures + 1,
                enabled = consecutive_failures + 1 < %(limit)s,
                disabled_at = CASE WHEN consecutive_failures + 1 >= %(limit)s THEN now() END,
                etag = CASE WHEN consecutive_failures + 1 < %(limit)s THEN etag END
            WHERE id = %(id)s
            RETURNING enabled
            """,
            {"limit": DISABLE_AFTER_FAILURES, "id": outcome.board.id},
        )
        (enabled,) = await cursor.fetchone()
        if not enabled:
            outcome.store.closed = await close_board(conn, outcome.board)


async def run_ingestion(
    pool: AsyncConnectionPool,
    client: httpx.AsyncClient,
    ats: tuple[str, ...] = tuple(ADAPTERS),
    limit: int | None = None,
    trigger: str = "manual",
    ingestor: Ingestor | None = None,
    full: bool = False,
) -> RunSummary:
    async with await psycopg.AsyncConnection.connect(
        pool.conninfo, autocommit=True
    ) as lock:
        cursor = await lock.execute("SELECT pg_try_advisory_lock(%s)", (RUN_LOCK_ID,))
        (acquired,) = await cursor.fetchone()
        if not acquired:
            raise RunInProgress("another ingestion run is in progress")
        # Holding the lock means no run is active, so any run still marked running was killed.
        await lock.execute(
            "UPDATE ingestion_runs SET status = 'failed', finished_at = now()"
            " WHERE status = 'running'"
        )
        return await _run_locked(pool, client, ats, limit, trigger, ingestor, full)


async def _run_locked(
    pool: AsyncConnectionPool,
    client: httpx.AsyncClient,
    ats: tuple[str, ...],
    limit: int | None,
    trigger: str,
    ingestor: Ingestor | None,
    full: bool,
) -> RunSummary:
    started = time.perf_counter()
    async with pool.connection() as conn:
        await fill_missing_countries(conn)
        cursor = await conn.execute(
            "INSERT INTO ingestion_runs (trigger) VALUES (%s) RETURNING id", (trigger,)
        )
        (run_id,) = await cursor.fetchone()
        cursor = await conn.execute(
            """
            SELECT id, company_id, ats, board_token, api_region,
                   CASE WHEN %s THEN NULL ELSE etag END
            FROM job_boards
            WHERE (enabled OR disabled_at < now() - %s::interval) AND ats = ANY(%s)
            ORDER BY id
            LIMIT %s
            """,
            (full, RECHECK_DISABLED_AFTER, list(ats), limit),
        )
        boards = [Board(*row) for row in await cursor.fetchall()]

    outcomes = await (ingestor or Ingestor(pool, client)).run(run_id, boards)
    succeeded = sum(outcome.status == "succeeded" for outcome in outcomes)
    if succeeded == len(outcomes):
        status = "succeeded"
    elif succeeded == 0:
        status = "failed"
    else:
        status = "partial"

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE ingestion_runs SET status = %s, finished_at = now() WHERE id = %s",
            (status, run_id),
        )
    return RunSummary(run_id, status, outcomes, time.perf_counter() - started)


async def fill_missing_countries(conn: psycopg.AsyncConnection) -> int:
    """Works out countries for postings stored before countries were recorded."""
    filled = 0
    while True:
        cursor = await conn.execute(
            """
            SELECT j.id, s.source, s.raw FROM jobs j
            JOIN LATERAL (
                SELECT source, raw FROM job_sources WHERE job_id = j.id ORDER BY id LIMIT 1
            ) s ON true
            WHERE j.countries IS NULL
            LIMIT %s
            """,
            (COUNTRY_BATCH,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return filled
        updates = []
        for job_id, source, raw in rows:
            try:
                posting = ADAPTERS[source].parse(raw)
            except ValidationError:
                updates.append(([], job_id))
                continue
            updates.append(
                (posting_countries(posting.locations, posting.country_hints), job_id)
            )
        async with conn.transaction(), conn.cursor() as cursor:
            await cursor.executemany(
                "UPDATE jobs SET countries = %s WHERE id = %s", updates
            )
        filled += len(updates)


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUSES
    return isinstance(error, httpx.TransportError)


def _is_gone(error: Exception) -> bool:
    return (
        isinstance(error, httpx.HTTPStatusError)
        and error.response.status_code in GONE_STATUSES
    )


def _retry_after_seconds(error: Exception) -> float | None:
    if isinstance(error, httpx.HTTPStatusError):
        value = error.response.headers.get("retry-after", "")
        if value.isdigit():
            return float(value)
    return None


def _describe(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    return f"{type(error).__name__}: {error}"


def _total(outcomes: list[BoardOutcome], counter: str) -> int:
    return sum(getattr(outcome.store, counter) for outcome in outcomes)


def format_summary(summary: RunSummary) -> str:
    outcomes = summary.outcomes
    ok = [o for o in outcomes if o.status == "succeeded"]
    headline = (
        f"run {summary.run_id} {summary.status} in {summary.duration_s:.1f}s: "
        f"{len(ok)}/{len(outcomes)} boards ok "
        f"({sum(o.not_modified for o in ok)} unchanged since last fetch), "
        f"{sum(o.fetched for o in ok)} postings read "
        f"({_total(ok, 'inserted')} new, {_total(ok, 'updated')} updated, "
        f"{_total(ok, 'closed')} closed, {_total(ok, 'reopened')} reopened, "
        f"{sum(o.rejected for o in ok)} rejected); "
        f"descriptions {sum(o.descriptions_fetched for o in ok)} stored, "
        f"{sum(o.descriptions_failed for o in ok)} failed"
    )
    lines = [headline]
    failures: dict[str, int] = {}
    for outcome in outcomes:
        if outcome.error:
            failures[outcome.error] = failures.get(outcome.error, 0) + 1
    for error, count in sorted(failures.items(), key=lambda item: -item[1]):
        lines.append(f"  {count} boards failed: {error}")
    return "\n".join(lines)


def create_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT})


async def _main(limit: int | None, full: bool, trigger: str) -> RunSummary:
    async with (
        AsyncConnectionPool(
            settings.database_url, min_size=1, max_size=8, open=False
        ) as pool,
        create_client() as client,
    ):
        return await run_ingestion(
            pool, client, limit=limit, full=full, trigger=trigger
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch postings from enabled job boards."
    )
    parser.add_argument("--limit", type=int, help="only ingest the first N boards")
    parser.add_argument(
        "--full",
        action="store_true",
        help="ignore stored ETags and re-read every board",
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="record the run as scheduled, not manual",
    )
    args = parser.parse_args()
    trigger = "schedule" if args.scheduled else "manual"
    try:
        summary = asyncio.run(_main(args.limit, args.full, trigger))
    except RunInProgress as error:
        sys.exit(str(error))
    print(format_summary(summary))
    if summary.status == "failed":
        sys.exit(1)
