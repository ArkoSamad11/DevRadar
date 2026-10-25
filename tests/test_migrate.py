from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from devradar.migrate import MIGRATIONS_DIR, ChecksumMismatch, migrate

BUNDLED = sorted(path.stem for path in MIGRATIONS_DIR.glob("*.sql"))


def write_migration(directory, name, body):
    (directory / f"{name}.sql").write_text(body)


def table_exists(conninfo, table):
    with psycopg.connect(conninfo) as conn:
        return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[
            0
        ]


def test_applies_bundled_migrations_to_empty_database(empty_db):
    assert migrate(empty_db) == BUNDLED
    with psycopg.connect(empty_db) as conn:
        assert conn.execute("SELECT count(*) FROM categories").fetchone()[0] == 8


def test_rerun_applies_nothing(empty_db):
    migrate(empty_db)
    assert migrate(empty_db) == []


def test_applies_only_new_migrations(empty_db, tmp_path):
    write_migration(tmp_path, "0001_a", "CREATE TABLE a (id int);")
    migrate(empty_db, tmp_path)
    write_migration(tmp_path, "0002_b", "CREATE TABLE b (id int);")
    assert migrate(empty_db, tmp_path) == ["0002_b"]


def test_rejects_migration_edited_after_apply(empty_db, tmp_path):
    write_migration(tmp_path, "0001_a", "CREATE TABLE a (id int);")
    migrate(empty_db, tmp_path)
    write_migration(tmp_path, "0001_a", "CREATE TABLE a (id bigint);")
    with pytest.raises(ChecksumMismatch):
        migrate(empty_db, tmp_path)


def test_failed_migration_rolls_back_entirely(empty_db, tmp_path):
    write_migration(tmp_path, "0001_a", "CREATE TABLE a (id int);")
    write_migration(tmp_path, "0002_broken", "CREATE TABLE b (id int); SELECT 1 / 0;")
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrate(empty_db, tmp_path)
    assert table_exists(empty_db, "a")
    assert not table_exists(empty_db, "b")
    with psycopg.connect(empty_db) as conn:
        recorded = conn.execute("SELECT version FROM schema_migrations").fetchall()
    assert recorded == [("0001_a",)]


def test_concurrent_runs_apply_each_migration_once(empty_db):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: migrate(empty_db), range(4)))
    assert sorted(results) == [[], [], [], BUNDLED]
