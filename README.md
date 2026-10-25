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
uv run devradar-discover
uv run devradar-ingest
uv run pytest
uv run uvicorn devradar.api.main:app --reload
```

On an iCloud-synced folder (e.g. Desktop), keep the virtualenv out of sync before `uv sync`:

```bash
mkdir .venv.nosync && ln -s .venv.nosync .venv
```

## Discovery

`uv run devradar-discover` reads the community internship lists
([SimplifyJobs](https://github.com/SimplifyJobs/Summer2027-Internships) and
[vanshb03](https://github.com/vanshb03/Summer2027-Internships)), resolves short links, and turns
each posting URL into a pollable job board (Greenhouse, Lever, Ashby or Workday). The lists are
used to find *which boards exist*; postings themselves are fetched from the boards directly.

## Ingestion

`uv run devradar-ingest` polls every enabled board with a supported adapter (currently Greenhouse).
Each run lists all postings per board, writes only new or changed ones, and fetches descriptions
only for internships that are new or edited. Boards are fetched concurrently (16 requests in flight)
with retries on timeouts, 429s and 5xx; a failing board is recorded in `board_fetches` without
affecting the others. A posting missing from two consecutive successful fetches is marked closed.

## Migrations

Schema changes are plain SQL files in `src/devradar/migrations/`, applied in filename order by
`uv run devradar-migrate`. Each file runs in its own transaction and is recorded with a checksum in
`schema_migrations`; editing an already-applied file is an error, so changes go in a new file.
