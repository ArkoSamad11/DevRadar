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

from devradar.config import settings
from devradar.discovery.run import USER_AGENT
from devradar.ingestion.ashby import AshbyAdapter
from devradar.ingestion.greenhouse import GreenhouseAdapter
from devradar.ingestion.models import Adapter, Board, MalformedResponse
from devradar.ingestion.store import (
    DescriptionTask,
    StoreResult,
    store_description,
    store_postings,
)

ADAPTERS: dict[str, Adapter] = {
    "greenhouse": GreenhouseAdapter(),
    "ashby": AshbyAdapter(),
}
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
DISABLE_AFTER_FAILURES = 5


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


class FetchFailed(Exception):
    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


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
                raise FetchFailed(_describe(error), attempt) from error
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
        self.http_slots = asyncio.Semaphore(http_concurrency)
        self.retry = retry

    async def run(self, run_id: int, boards: list[Board]) -> list[BoardOutcome]:
        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(self.ingest_board(run_id, board)) for board in boards
            ]
        return [task.result() for task in tasks]

    async def ingest_board(self, run_id: int, board: Board) -> BoardOutcome:
        started = time.perf_counter()
        try:
            outcome = await self._ingest(board)
        except Exception as error:  # noqa: BLE001
            # A bug triggered by one board's data must not cancel the other boards' tasks.
            outcome = BoardOutcome(
                board, "failed", 1, error=f"{type(error).__name__}: {error}"
            )
        outcome.duration_ms = int((time.perf_counter() - started) * 1000)
        await self._record(run_id, outcome)
        return outcome

    async def _ingest(self, board: Board) -> BoardOutcome:
        adapter = ADAPTERS[board.ats]
        try:
            (postings, rejected), attempts = await with_retries(
                lambda: self._limited(adapter.fetch_postings(self.client, board)),
                self.retry,
            )
        except FetchFailed as failure:
            return BoardOutcome(board, "failed", failure.attempts, error=str(failure))

        outcome = BoardOutcome(board, "succeeded", attempts, len(postings), rejected)
        async with self.pool.connection() as conn:
            outcome.store = await store_postings(conn, board, postings)
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
                    adapter.fetch_description(self.client, board, task.external_id)
                ),
                self.retry,
            )
        except FetchFailed:
            return False
        async with self.pool.connection() as conn:
            await store_description(conn, task, description)
        return True

    async def _limited[T](self, request: Awaitable[T]) -> T:
        async with self.http_slots:
            return await request

    async def _record(self, run_id: int, outcome: BoardOutcome) -> None:
        store = outcome.store
        try:
            async with self.pool.connection() as conn, conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO board_fetches (
                        run_id, job_board_id, status, attempts, fetched_count, inserted_count,
                        updated_count, unchanged_count, closed_count, reopened_count,
                        rejected_count, descriptions_fetched, descriptions_failed,
                        duration_ms, error
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    ),
                )
                if outcome.status == "succeeded":
                    await conn.execute(
                        """
                        UPDATE job_boards SET consecutive_failures = 0, last_success_at = now()
                        WHERE id = %s
                        """,
                        (outcome.board.id,),
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE job_boards
                        SET consecutive_failures = consecutive_failures + 1,
                            enabled = consecutive_failures + 1 < %s
                        WHERE id = %s
                        """,
                        (DISABLE_AFTER_FAILURES, outcome.board.id),
                    )
        except psycopg.Error as error:
            print(
                f"could not record fetch for board {outcome.board.id}: {error}",
                file=sys.stderr,
            )


async def run_ingestion(
    pool: AsyncConnectionPool,
    client: httpx.AsyncClient,
    ats: tuple[str, ...] = tuple(ADAPTERS),
    limit: int | None = None,
    trigger: str = "manual",
    ingestor: Ingestor | None = None,
) -> RunSummary:
    started = time.perf_counter()
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO ingestion_runs (trigger) VALUES (%s) RETURNING id", (trigger,)
        )
        (run_id,) = await cursor.fetchone()
        cursor = await conn.execute(
            """
            SELECT id, company_id, ats, board_token, api_region FROM job_boards
            WHERE enabled AND ats = ANY(%s)
            ORDER BY id
            LIMIT %s
            """,
            (list(ats), limit),
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


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUSES
    return isinstance(error, httpx.TransportError)


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


def _print_summary(summary: RunSummary) -> None:
    outcomes = summary.outcomes
    ok = [o for o in outcomes if o.status == "succeeded"]
    print(
        f"run {summary.run_id} {summary.status} in {summary.duration_s:.1f}s: "
        f"{len(ok)}/{len(outcomes)} boards ok, {sum(o.fetched for o in ok)} postings "
        f"({_total(ok, 'inserted')} new, {_total(ok, 'updated')} updated, {_total(ok, 'unchanged')} unchanged, "
        f"{_total(ok, 'closed')} closed, {_total(ok, 'reopened')} reopened, "
        f"{sum(o.rejected for o in ok)} rejected); "
        f"descriptions {sum(o.descriptions_fetched for o in ok)} stored, "
        f"{sum(o.descriptions_failed for o in ok)} failed"
    )
    failures: dict[str, int] = {}
    for outcome in outcomes:
        if outcome.error:
            failures[outcome.error] = failures.get(outcome.error, 0) + 1
    for error, count in sorted(failures.items(), key=lambda item: -item[1]):
        print(f"  {count} boards failed: {error}")


async def _main(limit: int | None) -> RunSummary:
    async with (
        AsyncConnectionPool(
            settings.database_url, min_size=1, max_size=8, open=False
        ) as pool,
        httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as client,
    ):
        return await run_ingestion(pool, client, limit=limit)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch postings from enabled job boards."
    )
    parser.add_argument("--limit", type=int, help="only ingest the first N boards")
    args = parser.parse_args()
    _print_summary(asyncio.run(_main(args.limit)))
