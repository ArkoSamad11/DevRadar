# DevRadar

Ingestion pipeline and query API for tech internship postings.

DevRadar discovers companies from community-maintained internship lists, polls each company's
applicant-tracking-system job board directly, normalizes postings into one schema in PostgreSQL,
and serves them through a FastAPI service.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
```
