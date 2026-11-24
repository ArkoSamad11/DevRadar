from psycopg_pool import AsyncConnectionPool

from devradar.config import settings

STATEMENT_TIMEOUT_MS = 5000


def create_pool(conninfo: str = settings.database_url) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"},
    )
