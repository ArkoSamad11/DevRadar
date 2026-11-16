import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable

import psycopg
from psycopg_pool import AsyncConnectionPool

from devradar.config import settings
from devradar.discovery.run import discover
from devradar.ingestion.worker import (
    RunInProgress,
    create_client,
    format_summary,
    run_ingestion,
)

log = logging.getLogger("devradar.scheduler")

Step = Callable[[], Awaitable[None]]


async def run_forever(
    ingest: Step,
    discover_boards: Step,
    ingest_interval: float,
    discovery_interval: float,
    stop: asyncio.Event,
) -> None:
    next_discovery = time.monotonic()
    while not stop.is_set():
        cycle_started = time.monotonic()
        if cycle_started >= next_discovery:
            await _guarded("discovery", discover_boards)
            next_discovery = cycle_started + discovery_interval
        await _guarded("ingestion", ingest)

        remaining = ingest_interval - (time.monotonic() - cycle_started)
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(remaining, 0))
        except TimeoutError:
            pass


async def _guarded(name: str, step: Step) -> None:
    try:
        await step()
    except RunInProgress as error:
        log.info("skipped %s: %s", name, error)
    except Exception:
        # One failed cycle (e.g. the database restarting) must not stop future cycles.
        log.exception("%s failed", name)


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)

    async with (
        AsyncConnectionPool(
            settings.database_url, min_size=1, max_size=8, open=False
        ) as pool,
        create_client() as client,
    ):

        async def ingest() -> None:
            summary = await run_ingestion(pool, client, trigger="schedule")
            log.info(format_summary(summary))

        async def discover_boards() -> None:
            async with await psycopg.AsyncConnection.connect(
                settings.database_url, autocommit=True
            ) as conn:
                for stats in await discover(conn, client):
                    log.info("discovery %s", stats)

        log.info(
            "scheduler started: ingest every %s min, discovery every %s h",
            settings.ingest_interval_minutes,
            settings.discovery_interval_hours,
        )
        await run_forever(
            ingest,
            discover_boards,
            settings.ingest_interval_minutes * 60,
            settings.discovery_interval_hours * 3600,
            stop,
        )
        log.info("scheduler stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(_main())
