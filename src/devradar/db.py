from psycopg_pool import AsyncConnectionPool

from devradar.config import settings

STATEMENT_TIMEOUT_MS = 5000


def create_pool(conninfo: str = settings.database_url) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={
            # A plan cached while searching common words is very slow for rare ones.
            "options": (
                f"-c statement_timeout={STATEMENT_TIMEOUT_MS} -c plan_cache_mode=force_custom_plan"
            )
        },
    )
