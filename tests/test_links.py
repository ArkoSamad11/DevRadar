import httpx
import psycopg
import pytest
import respx

from devradar.discovery.links import resolve_links

pytestmark = pytest.mark.anyio

SHORT = "https://go.vansh.nyc/a"
TARGET = "https://jobs.lever.co/acme/1"


async def resolve(conninfo, urls):
    async with (
        await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn,
        httpx.AsyncClient() as client,
    ):
        return await resolve_links(conn, client, urls)


@respx.mock
async def test_resolves_redirect_once_then_serves_from_cache(migrated_db):
    route = respx.get(SHORT).mock(
        return_value=httpx.Response(302, headers={"location": TARGET})
    )
    assert await resolve(migrated_db, [SHORT, SHORT]) == {SHORT: TARGET}
    assert await resolve(migrated_db, [SHORT]) == {SHORT: TARGET}
    assert route.call_count == 1


@respx.mock
async def test_relative_redirect_is_made_absolute(migrated_db):
    respx.get(SHORT).mock(return_value=httpx.Response(301, headers={"location": "/b"}))
    assert await resolve(migrated_db, [SHORT]) == {SHORT: "https://go.vansh.nyc/b"}


@respx.mock
async def test_dead_link_is_cached_as_unresolvable(migrated_db):
    route = respx.get(SHORT).mock(return_value=httpx.Response(404))
    assert await resolve(migrated_db, [SHORT]) == {SHORT: None}
    assert await resolve(migrated_db, [SHORT]) == {SHORT: None}
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectTimeout("timed out"), httpx.Response(503), httpx.Response(429)],
)
async def test_transient_failure_is_retried_on_next_run(migrated_db, failure):
    route = respx.get(SHORT)
    route.side_effect = [failure, httpx.Response(302, headers={"location": TARGET})]
    assert await resolve(migrated_db, [SHORT]) == {}
    assert await resolve(migrated_db, [SHORT]) == {SHORT: TARGET}
    assert route.call_count == 2
