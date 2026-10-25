import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg

from devradar.config import settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION_LOCK_ID = 7206311


class ChecksumMismatch(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def load_migrations(directory: Path) -> list[Migration]:
    return [
        Migration(path.stem, path.read_text())
        for path in sorted(directory.glob("*.sql"))
    ]


def migrate(conninfo: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    with psycopg.connect(conninfo, autocommit=True) as conn:
        # Session-level lock: released automatically when the connection closes.
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     text PRIMARY KEY,
                checksum    text NOT NULL,
                applied_at  timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = dict(
            conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        )

        newly_applied = []
        for migration in load_migrations(directory):
            if migration.version in applied:
                if applied[migration.version] != migration.checksum:
                    raise ChecksumMismatch(
                        f"{migration.version} was edited after being applied"
                    )
                continue
            with conn.transaction():
                conn.execute(migration.sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (migration.version, migration.checksum),
                )
            newly_applied.append(migration.version)
        return newly_applied


def main() -> None:
    applied = migrate(settings.database_url)
    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("Database is up to date")


if __name__ == "__main__":
    main()
