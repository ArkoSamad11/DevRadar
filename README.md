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

`uv run devradar-ingest` polls every enabled Greenhouse, Ashby and Lever board. Each source is an
adapter in `src/devradar/ingestion/` that turns its API's records into a common `Posting`; adding a
source means writing an adapter and registering it, with no schema, store or worker changes.

Each run writes only new or changed postings. Descriptions are kept for internships only: Ashby and
Lever include them in their listings, while Greenhouse descriptions are fetched per posting, only
when an internship is new or edited. Requests are limited per source (16 in flight, 8 for Lever) and
retried on timeouts, 429s and 5xx; a failing board is recorded in `board_fetches` without affecting
the others, and is disabled after five consecutive failures. A posting missing from two consecutive
successful fetches is marked closed.

## Classification

Postings are tagged as internships and with role categories (software, ML/AI, data science,
systems, security, quant, operations research, math) from their titles, plus descriptions for
operations research and math. `scripts/evaluate_classifier.py` scores the rules against 200
hand-labeled real titles (`tests/fixtures/classifier_sample.csv`), tuned on one half and measured on
the other. After changing the rules, `uv run devradar-reclassify` re-applies them to stored postings
without refetching anything.

## Migrations

Schema changes are plain SQL files in `src/devradar/migrations/`, applied in filename order by
`uv run devradar-migrate`. Each file runs in its own transaction and is recorded with a checksum in
`schema_migrations`; editing an already-applied file is an error, so changes go in a new file.
