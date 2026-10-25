from psycopg_pool import AsyncConnectionPool

from devradar.config import settings

pool = AsyncConnectionPool(settings.database_url, open=False)
