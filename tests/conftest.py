import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from devradar.config import settings
from devradar.migrate import migrate


@pytest.fixture
def empty_db():
    name = f"devradar_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(settings.database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield make_conninfo(settings.database_url, dbname=name)
    with psycopg.connect(settings.database_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture
def migrated_db(empty_db):
    migrate(empty_db)
    return empty_db
