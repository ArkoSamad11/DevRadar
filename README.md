# DevRadar

Ingestion pipeline and query API for tech internship postings.

DevRadar discovers companies from community-maintained internship lists, polls each company's
applicant-tracking-system job board directly, normalizes postings into one schema in PostgreSQL,
and serves them through a FastAPI service.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
docker compose up -d --wait
cp .env.example .env
uv run devradar-migrate
uv run pytest
uv run uvicorn devradar.api.main:app --reload
```

On an iCloud-synced folder (e.g. Desktop), keep the virtualenv out of sync before `uv sync`:

```bash
mkdir .venv.nosync && ln -s .venv.nosync .venv
```

## Migrations

Schema changes are plain SQL files in `src/devradar/migrations/`, applied in filename order by
`uv run devradar-migrate`. Each file runs in its own transaction and is recorded with a checksum in
`schema_migrations`; editing an already-applied file is an error, so changes go in a new file.
