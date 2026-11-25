# DevRadar

Ingestion pipeline and query API for tech internship postings.

DevRadar discovers companies from community-maintained internship lists, polls each company's
applicant-tracking-system job board directly, normalizes postings into one schema in PostgreSQL,
and serves them through a FastAPI service.

**Live site:** [https://api-production-45e4e6e8.up.railway.app](https://api-production-45e4e6e8.up.railway.app)
· API docs: [https://api-production-45e4e6e8.up.railway.app/docs](https://api-production-45e4e6e8.up.railway.app/docs)
([health](https://api-production-45e4e6e8.up.railway.app/health), [recent runs](https://api-production-45e4e6e8.up.railway.app/ingestion-runs))

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

## API

`uv run uvicorn devradar.api.main:app` serves the API; interactive docs are at `/docs`.

| Endpoint | Purpose |
|---|---|
| `GET /jobs` | Search postings, newest first. Filters: `q` (title or description), `category` (repeatable), `location`, `remote`, `company`, `internships_only` (default true), `include_closed`. |
| `GET /jobs/{id}` | One posting with its description, locations, categories and sources. |
| `POST /match` | Rank postings against a resume (PDF or plain text, up to 1 MB). Filters: `kind`, `degree`, `term`, `category`, `location`, `remote`. |
| `GET /internships` | Every active internship in one cacheable response, revalidated with an ETag. |
| `GET /companies` | Tracked companies with active job and internship counts; `q`, `hiring_interns`. |
| `GET /sources` | Board health per ATS, including discovered-but-unsupported sources. |
| `GET /ingestion-runs` | Pipeline run history with per-run totals. |
| `GET /health` | Database health, plus `stale` when ingestion has missed three intervals. |

Lists return `{"items": [...], "limit": n, "next_cursor": "..."}`; pass `next_cursor` back as `cursor`
for the next page. Cursors are keyset-based, so pages stay consistent while new postings arrive.

```bash
curl 'localhost:8000/jobs?q=python&category=ml_ai&location=new%20york&limit=5'
```

On a fixed copy of the real data (80,038 jobs), p95 latency fell from 367 ms to 8 ms for companies
hiring interns and from 130 ms to 5 ms for rare keywords. One search got slower; see the
[benchmark report](docs/benchmark.md).

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
when an internship is new or edited. Requests are limited per source (one per second for Lever, at most 8 at once for Greenhouse and 16 for
Ashby) and
retried on timeouts, 429s and 5xx; a failing board is recorded in `board_fetches` without affecting
the others. Five consecutive 404s disable a board and close its postings; timeouts and 5xx never do,
and disabled boards are rechecked daily. A posting missing from two consecutive successful fetches
is marked closed, and a 304 counts as confirming an earlier miss.

## Keeping postings current

`uv run devradar-scheduler` runs discovery at startup and then daily, and ingestion every hour
(`INGEST_INTERVAL_MINUTES`, `DISCOVERY_INTERVAL_HOURS`). Boards are polled with conditional requests:
each board's ETag is stored and sent back as `If-None-Match`, so an unchanged board answers `304 Not
Modified` with no body and costs no parsing or writes. In a 15-minute window only 9 of 990 boards
changed, and a conditional sweep downloaded 5 MB instead of 351 MB.

`docker compose up -d` runs the database and the scheduler as a `worker` container.

Only one ingestion run executes at a time (a Postgres advisory lock), so a manual
`devradar-ingest` started during a scheduled run exits instead of repeating its work. Use
`devradar-ingest --full` to ignore stored ETags after changing parsing logic.

## Deployment

Hosted on Railway from one Docker image: a Postgres database reachable only on the private network,
the API (which runs migrations before each deploy), and two cron jobs that exit between runs:
`devradar-ingest --scheduled` hourly and `devradar-discover` daily. Cron is used instead of
`devradar-scheduler`, which remains for running everything locally.

## Classification

Postings are tagged as internships and with role categories (software, ML/AI, data science,
systems, security, quant, operations research, math) from their titles, plus descriptions for
operations research and math. `scripts/evaluate_classifier.py` scores the rules against 200
hand-labeled real titles (`tests/fixtures/classifier_sample.csv`), tuned on one half and measured on
the other. After changing the rules, `uv run devradar-reclassify` re-applies them to stored postings
without refetching anything.

## Role model

`src/devradar/roles/` scores text against six roles with TF-IDF and one logistic regression per role.
It learns from internship and full-time descriptions, labeled by the title rules, and is evaluated on
internships of companies held out of training. Scores are calibrated, and each is explained by the
words behind it. Training uses scikit-learn; serving needs only NumPy (a 0.4 MB file, 24 MB of memory).

| Trained on | Mean average precision | Precision at 20 |
|---|---:|---:|
| 3,080 internships | 0.631 | 0.797 |
| 18,277 internships and full-time jobs | 0.662 | 0.844 |

These measure agreement with the title rules, not whether a person would want the job.

```bash
uv run python -m devradar.roles.train            # train and save the model
uv run python -m devradar.roles.train --compare  # compare training data choices
```

## Website

`GET /` serves a page built from plain HTML, CSS and JavaScript (`src/devradar/web/`). It loads every
internship once (about 200 KB compressed), filters them in the browser, and adds rows while scrolling.
Searching descriptions asks the server and fetches the next page ahead of time. A resume stays in the
open tab and nothing is saved in the browser.

## Resume matching

`POST /match` scores a resume's roles, drops postings that clearly state a requirement the applicant
does not meet, ranks the rest by word similarity and role fit, and explains each one with shared terms,
the degree and graduation date it asks for, and skills the resume lacks. Degrees and graduation dates
are read from titles and descriptions, terms and seniority from titles. Full-time postings are compared
by title only.

```bash
curl -H 'Content-Type: application/pdf' --data-binary @resume.pdf \
  'localhost:8000/match?degree=bachelors&graduation=2029-05&term=summer%202027'
```

Resumes are read in memory and discarded, never written to disk, the database or logs. PDFs are parsed
in a separate process with CPU, memory and time limits, and requests are limited by size, rate and
concurrency. The ranking weights are hand-set; match quality has not been measured on real resumes.

## Migrations

Schema changes are plain SQL files in `src/devradar/migrations/`, applied in filename order by
`uv run devradar-migrate`. Each file runs in its own transaction and is recorded with a checksum in
`schema_migrations`; editing an already-applied file is an error, so changes go in a new file.
