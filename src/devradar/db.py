from psycopg_pool import AsyncConnectionPool

from devradar.config import settings


def create_pool(conninfo: str = settings.database_url) -> AsyncConnectionPool:
    return AsyncConnectionPool(conninfo, min_size=1, max_size=10, open=False)
