import asyncio
import os
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest

from devradar.api import match
from devradar.api.main import create_app
from devradar.db import create_pool
from devradar.matching import index as index_module
from devradar.matching.index import build_index
from devradar.roles.model import load_model
from tests.test_matching_parts import make_pdf

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 25, tzinfo=UTC)
QUANT = (
    "We trade options and futures. Use probability, statistics, stochastic processes, Python and"
    " NumPy to research trading strategies with our market making team. "
) * 4
BACKEND = (
    "Build backend services in Python and Go with PostgreSQL, Docker and Kubernetes. You will work"
    " on distributed systems, databases and REST APIs. "
) * 4
LEGAL = "Draft briefs, research case law and support litigation with attorneys. " * 8
QUANT_RESUME = (
    "Mathematics and statistics student. Probability, stochastic processes, time series analysis."
    " Python, NumPy, pandas, C++. Trading competition finalist, market making game, options pricing"
    " with Monte Carlo simulation and calibrated probabilities for betting markets."
)
JOBS = [
    ("Quantitative Research Intern - Summer 2027", "acme", QUANT, "Chicago, IL", True),
    ("Quantitative Research Intern - Fall 2026", "acme", QUANT, "New York, NY", True),
    ("Quantitative Research Intern, PhD", "globex", QUANT, "New York, NY", True),
    (
        "Trading Intern",
        "initech",
        QUANT + "Must be a U.S. citizen.",
        "Remote - US",
        True,
    ),
    ("Backend Software Engineer Intern", "initech", BACKEND, "Austin, TX", True),
    ("Legal Intern", "globex", LEGAL, "Boston, MA", True),
    ("Quantitative Trader", "acme", None, "Chicago, IL", False),
    ("Senior Quantitative Trader", "acme", None, "Chicago, IL", False),
]


def add_jobs(conninfo, jobs=JOBS):
    with psycopg.connect(conninfo) as conn:
        for number, (title, company, description, location, internship) in enumerate(
            jobs
        ):
            (company_id,) = conn.execute(
                """
                INSERT INTO companies (name, slug) VALUES (%s, %s)
                ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug RETURNING id
                """,
                (company.title(), company),
            ).fetchone()
            (job_id,) = conn.execute(
                """
                INSERT INTO jobs (posting_key, company_id, title, url, description,
                                  is_internship, posted_at)
                VALUES (%s, %s, %s, 'https://x.test', %s, %s, %s) RETURNING id
                """,
                (
                    f"ashby:{company}-{title}",
                    company_id,
                    title,
                    description,
                    internship,
                    NOW - timedelta(hours=number),
                ),
            ).fetchone()
            conn.execute(
                "INSERT INTO job_locations (job_id, raw, is_remote) VALUES (%s, %s, %s)",
                (job_id, location, "Remote" in location),
            )
            conn.execute(
                """
                INSERT INTO job_sources (job_id, source, external_id, url, raw, content_hash)
                VALUES (%s, 'ashby', %s, 'https://x.test', '{}', 'h')
                """,
                (job_id, f"{company}-{title}"),
            )


@pytest.fixture
async def api(migrated_db):
    add_jobs(migrated_db)
    app = create_app()
    async with create_pool(migrated_db) as pool:
        app.state.pool = pool
        app.state.matcher = match.Matcher(pool)
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            client.app = app
            yield client


async def post(
    api, body=QUANT_RESUME, content_type="text/plain", headers=None, **params
):
    return await api.post(
        "/match",
        content=body,
        params=params,
        headers={"content-type": content_type, **(headers or {})},
    )


def titles(response):
    assert response.status_code == 200, response.text
    return [m["job"]["title"] for m in response.json()["matches"]]


async def test_ranks_relevant_postings_first_and_explains_them(api):
    body = (await post(api)).json()
    assert body["confident"] and body["roles"][0]["role"] == "quant"
    assert set(body["roles"][0]["evidence"]) & {"trading", "probability", "stochastic"}
    found = [m["job"]["title"] for m in body["matches"]]
    assert set(found[:4]) == {t for t, *_ in JOBS[:4]} and found[-1] == "Legal Intern"
    best = body["matches"][0]
    assert {"trading", "stochastic"} & set(best["matched_terms"])
    assert {"Python", "NumPy", "Probability"} <= set(best["matched_skills"])
    assert best["score"] > body["matches"][-1]["score"]


async def test_reports_skills_the_posting_wants_and_the_resume_lacks(api):
    body = (await post(api)).json()
    backend = next(m for m in body["matches"] if "Backend" in m["job"]["title"])
    assert backend["matched_skills"] == ["Python"]
    assert {"Docker", "Kubernetes", "PostgreSQL"} <= set(backend["missing_skills"])


async def test_flags_work_authorization_without_hiding_the_posting(api):
    flags = {
        m["job"]["title"]: m["mentions_work_authorization"]
        for m in (await post(api)).json()["matches"]
    }
    assert [title for title, flagged in flags.items() if flagged] == ["Trading Intern"]


async def test_accepts_a_pdf(api):
    words = QUANT_RESUME.split()
    lines = [" ".join(words[i : i + 8]) for i in range(0, len(words), 8)]
    response = await post(api, make_pdf(*lines), "application/pdf")
    assert response.json()["roles"][0]["role"] == "quant"


async def test_degree_and_term_filters_drop_only_stated_mismatches(api):
    assert "Quantitative Research Intern, PhD" not in titles(
        await post(api, degree="bachelors")
    )
    phd = titles(await post(api, degree="phd"))
    assert "Quantitative Research Intern, PhD" in phd and "Trading Intern" in phd
    summer = titles(await post(api, term="summer 2027"))
    assert (
        "Quantitative Research Intern - Fall 2026" not in summer
        and "Trading Intern" in summer
    )


STATED = [
    ("Trading Intern", "acme", QUANT + "Must be pursuing a PhD in mathematics."),
    (
        "Research Intern",
        "globex",
        QUANT
        + "Pursuing a Bachelor's or Master's, graduating between Dec 2027 and June 2028.",
    ),
    ("Strategy Intern", "initech", QUANT + "A PhD is a plus."),
]


async def test_descriptions_that_state_a_degree_or_graduation_date_decide(
    api, migrated_db
):
    with psycopg.connect(migrated_db) as conn:
        conn.execute("DELETE FROM jobs")
    add_jobs(migrated_db, [(*job, "Chicago, IL", True) for job in STATED])

    assert set(titles(await post(api))) == {title for title, *_ in STATED}
    bachelors = {"degree": "bachelors"}
    assert set(titles(await post(api, graduation=2028, **bachelors))) == {
        "Research Intern",
        "Strategy Intern",
    }
    assert titles(await post(api, graduation=2029, **bachelors)) == ["Strategy Intern"]
    assert "Research Intern" in titles(await post(api, degree="mba"))

    stated = {m["job"]["title"]: m for m in (await post(api)).json()["matches"]}
    assert stated["Trading Intern"]["degrees"] == ["phd"]
    assert stated["Trading Intern"]["graduation"] is None
    assert stated["Research Intern"]["degrees"] == ["bachelors", "masters"]
    assert stated["Research Intern"]["graduation"] == {
        "earliest": "2027-12-01",
        "latest": "2028-06-28",
    }
    assert stated["Strategy Intern"]["degrees"] == []


async def test_location_remote_and_category_filters(api):
    assert set(titles(await post(api, location="new york"))) == {JOBS[1][0], JOBS[2][0]}
    assert titles(await post(api, remote=True)) == ["Trading Intern"]
    assert titles(await post(api, category="math")) == []


async def test_full_time_matches_leave_out_senior_titles(api):
    assert titles(await post(api, kind="full_time")) == ["Quantitative Trader"]


async def test_repeated_postings_are_grouped_and_companies_are_capped(api, migrated_db):
    more = [("Trading Intern", "initech2", QUANT, f"City {i}", True) for i in range(1)]
    more += [
        (f"Trading Intern {name}", "initech2", QUANT, "X", True)
        for name in ("A", "B", "C", "D")
    ]
    add_jobs(migrated_db, more)
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            """
            INSERT INTO jobs (posting_key, company_id, title, url, description, is_internship)
            SELECT 'ashby:copy', company_id, title, url, description, true FROM jobs
            WHERE posting_key = 'ashby:initech-Trading Intern'
            """
        )
        conn.execute(
            """
            INSERT INTO job_sources (job_id, source, external_id, url, raw, content_hash)
            SELECT id, 'ashby', 'copy', url, '{}', 'h' FROM jobs WHERE posting_key = 'ashby:copy'
            """
        )
    matches = (await post(api, limit=50)).json()["matches"]
    companies = [m["job"]["company"]["slug"] for m in matches]
    assert companies.count("initech2") == index_module.MAX_PER_COMPANY
    trading = [m for m in matches if m["job"]["company"]["slug"] == "initech"]
    assert sum(len(m["similar_postings"]) for m in trading) == 1


async def test_unrelated_resume_is_reported_as_not_confident_with_low_scores(api):
    nursing = (
        "Nursing student with patient care, vital signs and clinical rotations. " * 5
    )
    body = (await post(api, nursing)).json()
    assert body["confident"] is False
    assert all(role["evidence"] == [] for role in body["roles"])
    assert max(m["score"] for m in body["matches"]) < 0.5


@pytest.mark.parametrize(
    ("body", "content_type", "expected"),
    [
        (b"too short", "text/plain", 422),
        (b"", "application/pdf", 422),
        (b"MZ executable", "application/pdf", 422),
        (b"%PDF-1.4 broken", "application/pdf", 422),
        (b"PK\x03\x04", "application/vnd.openxmlformats-officedocument", 415),
        (b"x " * 600_000, "text/plain", 413),
    ],
)
async def test_bad_uploads_are_rejected(api, body, content_type, expected):
    assert (await post(api, body, content_type)).status_code == expected


@pytest.mark.parametrize(
    "query",
    [
        "term=someday",
        "degree=none",
        "limit=0",
        "kind=all",
        "graduation=2029-05",
        "graduation=1999",
    ],
)
async def test_bad_parameters_are_rejected(api, query):
    response = await api.post(
        f"/match?{query}", content=QUANT_RESUME, headers={"content-type": "text/plain"}
    )
    assert response.status_code == 422


async def test_each_client_is_rate_limited(api):
    api.app.state.matcher.limiter.per_client = 2
    first = {"x-real-ip": "1.1.1.1"}
    codes = [(await post(api, headers=first)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert (await post(api, headers={"x-real-ip": "2.2.2.2"})).status_code == 200


async def test_busy_parser_answers_503_instead_of_queueing(api, monkeypatch):
    async def slow(pdf):
        await asyncio.sleep(0.2)
        return QUANT_RESUME

    monkeypatch.setattr(match, "extract_text", slow)
    responses = await asyncio.gather(
        *(post(api, b"%PDF-1.4", "application/pdf") for _ in range(3))
    )
    assert sorted(r.status_code for r in responses) == [200, 200, 503]


async def test_the_resume_is_not_kept_anywhere(
    api, migrated_db, tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    marker = "zebraquartzmarker"
    response = await post(api, QUANT_RESUME + f" {marker}")
    assert response.status_code == 200 and marker not in response.text
    assert os.listdir(tmp_path) == [] and marker not in caplog.text
    with psycopg.connect(migrated_db) as conn:
        tables = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
        for (table,) in tables:
            query = f"SELECT count(*) FROM {table} t WHERE t::text LIKE %s"
            assert conn.execute(query, (f"%{marker}%",)).fetchone() == (0,)
    assert marker not in repr(vars(api.app.state.matcher.indexes))


async def test_index_is_rebuilt_only_after_a_new_ingestion_run(
    api, migrated_db, monkeypatch
):
    builds = []

    async def counting(*args):
        builds.append(1)
        return await build_index(*args)

    monkeypatch.setattr(index_module, "build_index", counting)
    monkeypatch.setattr(index_module, "REFRESH_SECONDS", 0)
    await post(api)
    await post(api)
    assert len(builds) == 1
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO ingestion_runs (trigger, status, finished_at)"
            " VALUES ('schedule', 'succeeded', now())"
        )
    await post(api)
    assert len(builds) == 2


async def test_empty_database_returns_no_matches(migrated_db):
    async with create_pool(migrated_db) as pool:
        index = await build_index(pool, load_model(), True)
    assert index.rank(load_model(), QUANT_RESUME, 10) == []


async def test_posting_closed_after_indexing_is_left_out(api, migrated_db):
    assert "Trading Intern" in titles(await post(api))
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'closed', closed_at = now() WHERE title = 'Trading Intern'"
        )
    assert "Trading Intern" not in titles(await post(api))
