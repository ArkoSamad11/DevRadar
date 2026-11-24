"""Compare API latency between two copies of the database, e.g. without and with indexes.

usage: uv run python scripts/benchmark.py BEFORE_DB AFTER_DB [--before-src DIR] [--out FILE]
"""

import argparse
import asyncio
import json
import os
import random
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass

import httpx
import psycopg
from psycopg.conninfo import make_conninfo

from devradar.api.pagination import encode_cursor
from devradar.config import settings

CONCURRENCY_LEVELS = (1, 8)
WARMUP_REQUESTS = 30
DEEP_PAGE_OFFSET = 40_000


@dataclass(frozen=True)
class Scenario:
    name: str
    urls: tuple[str, ...]


def build_scenarios(conninfo: str) -> list[Scenario]:
    with psycopg.connect(conninfo) as conn:
        listed_at, job_id = conn.execute(
            """
            SELECT listed_at, id FROM jobs WHERE status = 'active'
            ORDER BY listed_at DESC, id DESC OFFSET %s LIMIT 1
            """,
            (DEEP_PAGE_OFFSET,),
        ).fetchone()
        job_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM jobs WHERE is_internship ORDER BY md5(id::text) LIMIT 50"
            )
        ]
        companies = [
            row[0]
            for row in conn.execute(
                """
                SELECT c.slug FROM companies c JOIN jobs j ON j.company_id = c.id
                WHERE j.status = 'active' AND j.is_internship
                GROUP BY c.slug ORDER BY md5(c.slug) LIMIT 50
                """
            )
        ]
    cursor = encode_cursor(listed_at, job_id)
    return [
        Scenario("newest internships", ("/jobs?limit=20",)),
        Scenario("newest, all jobs", ("/jobs?internships_only=false&limit=20",)),
        Scenario(
            "page 2000 by cursor",
            (f"/jobs?internships_only=false&limit=20&cursor={cursor}",),
        ),
        Scenario(
            "category",
            tuple(
                f"/jobs?category={c}"
                for c in ("software", "ml_ai", "systems", "quant", "math")
            ),
        ),
        Scenario(
            "keyword, common",
            tuple(f"/jobs?q={q}" for q in ("python", "rust", "machine%20learning")),
        ),
        Scenario(
            "keyword, rare",
            tuple(f"/jobs?q={q}" for q in ("verilog", "kubernetes", "fortran")),
        ),
        Scenario("keyword, no match", ("/jobs?q=zzzznomatch", "/jobs?q=qqxxjj")),
        Scenario(
            "keyword, all jobs",
            tuple(
                f"/jobs?internships_only=false&q={q}" for q in ("verilog", "kubernetes")
            ),
        ),
        Scenario(
            "location",
            tuple(
                f"/jobs?location={c}" for c in ("new%20york", "chicago", "reykjavik")
            ),
        ),
        Scenario("remote", ("/jobs?remote=true",)),
        Scenario(
            "keyword + category + location",
            ("/jobs?q=python&category=ml_ai&location=new%20york",),
        ),
        Scenario(
            "keyword + broad filters, all jobs",
            (
                "/jobs?q=kubernetes&internships_only=false&location=united%20states",
                "/jobs?q=verilog&internships_only=false&remote=false",
                "/jobs?q=python&internships_only=false&category=software",
            ),
        ),
        Scenario("keyword, including closed", ("/jobs?q=verilog&include_closed=true",)),
        Scenario("one company's jobs", tuple(f"/jobs?company={c}" for c in companies)),
        Scenario("job detail", tuple(f"/jobs/{i}" for i in job_ids)),
        Scenario("companies hiring interns", ("/companies?hiring_interns=true",)),
        Scenario("companies", ("/companies",)),
        Scenario("sources", ("/sources",)),
    ]


class Server:
    def __init__(self, conninfo: str, port: int, source: str | None = None) -> None:
        self.base_url = f"http://127.0.0.1:{port}"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "devradar.api.main:app"]
            + ["--port", str(port), "--log-level", "warning"],
            env={
                **os.environ,
                "DATABASE_URL": conninfo,
                **({"PYTHONPATH": source} if source else {}),
            },
        )

    def wait_until_ready(self) -> None:
        for _ in range(100):
            try:
                if httpx.get(f"{self.base_url}/health").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        raise RuntimeError(f"{self.base_url} did not start")

    def stop(self) -> None:
        self.process.terminate()
        self.process.wait()


async def measure(
    base_url: str, scenario: Scenario, requests: int, concurrency: int, seed: int
) -> list[float]:
    urls = [scenario.urls[i % len(scenario.urls)] for i in range(requests)]
    random.Random(seed).shuffle(urls)
    pending = iter(urls)
    latencies = []

    async def worker(client: httpx.AsyncClient) -> None:
        for url in pending:
            started = time.perf_counter()
            response = await client.get(url)
            latencies.append((time.perf_counter() - started) * 1000)
            response.raise_for_status()

    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        await asyncio.gather(*(worker(client) for _ in range(concurrency)))
    return latencies


async def fetch_bodies(base_url: str, scenarios: list[Scenario]) -> dict[str, object]:
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        return {
            url: (await client.get(url)).json()
            for scenario in scenarios
            for url in scenario.urls
        }


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def summarize(latencies: list[float]) -> dict[str, float]:
    return {
        "n": len(latencies),
        "p50": round(statistics.median(latencies), 2),
        "p95": round(percentile(latencies, 0.95), 2),
        "p99": round(percentile(latencies, 0.99), 2),
    }


async def run(
    servers: dict[str, Server], scenarios: list[Scenario], requests: int, rounds: int
):
    samples: dict[tuple[str, str, int], list[float]] = {}
    for server in servers.values():
        for scenario in scenarios:
            await measure(server.base_url, scenario, WARMUP_REQUESTS, 8, seed=0)

    for round_number in range(1, rounds + 1):
        for scenario in scenarios:
            order = list(servers.items())
            if round_number % 2 == 0:
                order.reverse()
            for concurrency in CONCURRENCY_LEVELS:
                for label, server in order:
                    latencies = await measure(
                        server.base_url,
                        scenario,
                        requests,
                        concurrency,
                        seed=round_number,
                    )
                    samples.setdefault((scenario.name, label, concurrency), []).extend(
                        latencies
                    )
        print(f"round {round_number}/{rounds} done", file=sys.stderr, flush=True)
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument(
        "--before-src", help="src directory of the older code to run as BEFORE"
    )
    parser.add_argument("--out")
    args = parser.parse_args()

    databases = {"before": args.before, "after": args.after}
    conninfos = {
        k: make_conninfo(settings.database_url, dbname=v) for k, v in databases.items()
    }
    scenarios = build_scenarios(conninfos["before"])
    servers = {
        "before": Server(conninfos["before"], 8101, args.before_src),
        "after": Server(conninfos["after"], 8102),
    }
    try:
        for server in servers.values():
            server.wait_until_ready()
        bodies = [
            asyncio.run(fetch_bodies(s.base_url, scenarios)) for s in servers.values()
        ]
        different = [url for url in bodies[0] if bodies[0][url] != bodies[1][url]]
        if different:
            sys.exit(f"results differ between databases: {different}")
        print(f"results identical for {len(bodies[0])} URLs", file=sys.stderr)
        samples = asyncio.run(run(servers, scenarios, args.requests, args.rounds))
    finally:
        for server in servers.values():
            server.stop()

    results = []
    for scenario in scenarios:
        for concurrency in CONCURRENCY_LEVELS:
            before, after = (
                summarize(samples[scenario.name, label, concurrency])
                for label in databases
            )
            results.append(
                {
                    "scenario": scenario.name,
                    "concurrency": concurrency,
                    "before": before,
                    "after": after,
                }
            )
    for concurrency in CONCURRENCY_LEVELS:
        print(f"\nconcurrency {concurrency}")
        print(f"{'scenario':32} {'p50 ms':>18} {'p95 ms':>20}")
        for r in (r for r in results if r["concurrency"] == concurrency):
            b, a = r["before"], r["after"]
            print(
                f"{r['scenario']:32} {b['p50']:8.1f} -> {a['p50']:6.1f} "
                f"{b['p95']:9.1f} -> {a['p95']:6.1f}"
            )
    if args.out:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
