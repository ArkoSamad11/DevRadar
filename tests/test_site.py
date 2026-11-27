import gzip
import json
import re

import httpx
import psycopg
import pytest

from devradar.api.main import create_app
from devradar.api.site import WEB, Snapshot
from devradar.db import create_pool
from tests.test_api_match import add_jobs

pytestmark = pytest.mark.anyio


@pytest.fixture
async def api(migrated_db):
    add_jobs(migrated_db)
    app = create_app()
    async with create_pool(migrated_db) as pool:
        app.state.pool = pool
        app.state.snapshot = Snapshot()
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield client


async def test_snapshot_lists_active_internships_newest_first(api, migrated_db):
    jobs = (await api.get("/internships")).json()
    assert len(jobs) == 6 and all(j["is_internship"] for j in jobs)
    listed = [j["listed_at"] for j in jobs]
    assert listed == sorted(listed, reverse=True)
    assert jobs == (await api.get("/jobs?limit=100")).json()["items"]


async def test_snapshot_is_revalidated_with_an_etag_until_a_new_run(api, migrated_db):
    first = await api.get("/internships")
    assert "stale-while-revalidate" in first.headers["cache-control"]
    headers = {"if-none-match": first.headers["etag"]}
    unchanged = await api.get("/internships", headers=headers)
    assert (unchanged.status_code, unchanged.content) == (304, b"")
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO ingestion_runs (trigger, status, finished_at)"
            " VALUES ('schedule', 'succeeded', now())"
        )
        conn.execute(
            "UPDATE jobs SET status = 'closed', closed_at = now() WHERE title = 'Legal Intern'"
        )
    changed = await api.get("/internships", headers=headers)
    assert changed.status_code == 200 and len(changed.json()) == 5


async def test_snapshot_is_compressed_only_when_accepted(api):
    plain = await api.get("/internships", headers={"accept-encoding": "identity"})
    assert "content-encoding" not in plain.headers
    async with api.stream(
        "GET", "/internships", headers={"accept-encoding": "gzip"}
    ) as packed:
        raw = b"".join([chunk async for chunk in packed.aiter_raw()])
    assert json.loads(gzip.decompress(raw)) == plain.json()


@pytest.mark.parametrize("path", ["/", "/app.js", "/theme.js", "/styles.css"])
async def test_pages_are_served_with_a_strict_policy(api, path):
    response = await api.get(path)
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "unsafe" not in policy


async def test_other_files_are_not_served(api):
    for path in ("/index.html", "/../config.py", "/.env", "/app.py"):
        assert (await api.get(path)).status_code == 404


def test_page_has_no_inline_code_and_script_never_injects_html():
    html = (WEB / "index.html").read_text()
    script = (WEB / "app.js").read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)|\son\w+=|style=", html)
    assert not re.search(
        r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(", script
    )
    scripts = script + (WEB / "theme.js").read_text()
    assert not re.search(r"sessionStorage|indexedDB|document\.cookie", scripts)
    # The theme choice is the only thing kept in the browser.
    assert set(re.findall(r"localStorage\.\w+\(([^,)]*)", scripts)) == {
        '"devradar-theme"'
    }


def test_every_element_the_script_uses_exists():
    html = (WEB / "index.html").read_text()
    script = (WEB / "app.js").read_text()
    used = set(re.findall(r'\$\("([\w-]+)"\)', script))
    assert used <= set(re.findall(r'id="([\w-]+)"', html))
