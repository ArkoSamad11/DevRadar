import asyncio
import itertools

import pytest

from devradar.ingestion.worker import RunInProgress
from devradar.scheduler import run_forever

pytestmark = pytest.mark.anyio


async def run_for(seconds, ingest, discover, ingest_interval, discovery_interval):
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(seconds, stop.set)
    await asyncio.wait_for(
        run_forever(ingest, discover, ingest_interval, discovery_interval, stop),
        timeout=5,
    )


def recorder(log, name, error=None):
    async def step():
        log.append(name)
        if error:
            raise error

    return step


async def test_ingests_every_interval_and_discovers_less_often():
    log = []
    await run_for(0.27, recorder(log, "ingest"), recorder(log, "discover"), 0.05, 0.2)
    assert log[:2] == ["discover", "ingest"]
    assert log.count("discover") == 2
    assert 5 <= log.count("ingest") <= 7


async def test_failures_do_not_stop_the_schedule():
    log = []
    await run_for(
        0.12,
        recorder(log, "ingest", RuntimeError("database restarting")),
        recorder(log, "discover", RunInProgress("busy")),
        0.05,
        10,
    )
    assert log.count("ingest") >= 2


async def test_interval_is_measured_from_cycle_start():
    started = []

    async def slow_ingest():
        started.append(asyncio.get_running_loop().time())
        await asyncio.sleep(0.04)

    await run_for(0.25, slow_ingest, recorder([], "discover"), 0.1, 10)
    gaps = [b - a for a, b in itertools.pairwise(started)]
    assert gaps and all(0.08 < gap < 0.13 for gap in gaps)


async def test_stop_ends_the_wait_immediately():
    loop = asyncio.get_running_loop()
    began = loop.time()
    await run_for(0.05, recorder([], "ingest"), recorder([], "discover"), 60, 3600)
    assert loop.time() - began < 0.5
