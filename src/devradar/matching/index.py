import asyncio
import re
import time
from dataclasses import dataclass

import numpy as np
from psycopg_pool import AsyncConnectionPool

from devradar.ingestion.classify import categorize
from devradar.matching.eligibility import Degree, degrees_named, is_senior, terms_named
from devradar.roles.model import RoleModel
from devradar.roles.text import ROLES

BATCH = 200
MAX_PER_COMPANY = 3
CONFIDENT_ROLE_SCORE = 0.2
# Similarity of a strong match; a fixed scale keeps weak results from looking strong.
STRONG_SIMILARITY = 0.2
REFRESH_SECONDS = 300
NONE_NAMED: frozenset = frozenset()


@dataclass(frozen=True)
class Match:
    job_id: int
    score: float
    similarity: float
    role_fit: float
    matched_terms: list[str]
    duplicate_ids: list[int]


@dataclass(frozen=True)
class PostingIndex:
    """Word vectors, role scores and eligibility of active postings, newest first."""

    job_ids: np.ndarray
    company_ids: np.ndarray
    groups: np.ndarray
    rows: np.ndarray
    columns: np.ndarray
    values: np.ndarray
    starts: np.ndarray
    role_scores: np.ndarray
    degrees: list[set[Degree] | frozenset]
    terms: list[set[str] | frozenset]
    words: list[str]

    def rank(
        self,
        model: RoleModel,
        resume: str,
        limit: int,
        degree: Degree | None = None,
        term: str | None = None,
        allowed_ids: set[int] | None = None,
    ) -> list[Match]:
        columns, values = model.vectorize(resume)
        dense = np.zeros(len(model.vocabulary), dtype=np.float32)
        dense[columns] = values
        similarity = np.bincount(
            self.rows,
            weights=self.values * dense[self.columns],
            minlength=len(self.job_ids),
        )
        eligible = self._eligible(degree, term, allowed_ids)
        if not eligible.any():
            return []

        resume_roles = np.array(list(model.scores(resume).values()))
        best = resume_roles.max()
        role_fit = (self.role_scores * (resume_roles / best)).max(axis=1)
        confident = best >= CONFIDENT_ROLE_SCORE
        score = (similarity / STRONG_SIMILARITY + role_fit * confident) / 2

        order = np.argsort(-np.where(eligible, score, -1.0), kind="stable")
        return self._collect(
            order[: eligible.sum()], limit, score, similarity, role_fit, dense
        )

    def _eligible(
        self, degree: Degree | None, term: str | None, allowed_ids: set[int] | None
    ) -> np.ndarray:
        eligible = np.ones(len(self.job_ids), dtype=bool)
        if degree:
            eligible &= np.array(
                [not named or degree in named for named in self.degrees]
            )
        if term:
            eligible &= np.array([not named or term in named for named in self.terms])
        if allowed_ids is not None:
            eligible &= np.isin(self.job_ids, list(allowed_ids))
        return eligible

    def _collect(self, order, limit, score, similarity, role_fit, dense) -> list[Match]:
        matches: dict[int, Match] = {}
        per_company: dict[int, int] = {}
        for i in order:
            group, company = int(self.groups[i]), int(self.company_ids[i])
            if group in matches:
                matches[group].duplicate_ids.append(int(self.job_ids[i]))
                continue
            if len(matches) == limit or per_company.get(company, 0) == MAX_PER_COMPANY:
                continue
            per_company[company] = per_company.get(company, 0) + 1
            matches[group] = Match(
                job_id=int(self.job_ids[i]),
                score=round(min(float(score[i]), 1.0), 3),
                similarity=round(float(similarity[i]), 3),
                role_fit=round(float(role_fit[i]), 3),
                matched_terms=self._matched_terms(i, dense),
                duplicate_ids=[],
            )
        return list(matches.values())

    def _matched_terms(self, i: int, dense: np.ndarray, limit: int = 6) -> list[str]:
        start, end = self.starts[i], self.starts[i + 1]
        columns = self.columns[start:end]
        contributions = self.values[start:end] * dense[columns]
        top = np.argsort(-contributions)[:limit]
        return [self.words[columns[k]] for k in top if contributions[k] > 0]


def group_key(company_id: int, title: str) -> tuple[int, str]:
    return company_id, re.sub(r"[^a-z]+", " ", title.lower()).strip()


async def build_index(
    pool: AsyncConnectionPool, model: RoleModel, internships: bool
) -> PostingIndex:
    # Full-time descriptions are not stored, so those postings are matched on their titles.
    job_ids, company_ids, groups, columns, values, starts = [], [], [], [], [], [0]
    role_scores, degrees, terms = [], [], []
    known_groups: dict[tuple[int, str], int] = {}
    async with (
        pool.connection() as conn,
        conn.transaction(),
        conn.cursor(name="posting_index") as cursor,
    ):
        cursor.itersize = BATCH
        await cursor.execute(
            """
            SELECT id, company_id, title, CASE WHEN %s THEN coalesce(description, '') ELSE '' END
            FROM jobs WHERE status = 'active' AND is_internship = %s
            ORDER BY listed_at DESC, id DESC
            """,
            (internships, internships),
        )
        while batch := await cursor.fetchmany(BATCH):
            for job_id, company_id, title, description in batch:
                if not internships and is_senior(title):
                    continue
                text = f"{title}\n{description}"
                cols, vals = model.vectorize(text)
                job_ids.append(job_id)
                company_ids.append(company_id)
                groups.append(
                    known_groups.setdefault(
                        group_key(company_id, title), len(known_groups)
                    )
                )
                columns.append(cols.astype(np.int32))
                values.append(vals.astype(np.float32))
                starts.append(starts[-1] + len(cols))
                titled = categorize(title)
                predicted = (
                    model.scores(text) if description else dict.fromkeys(ROLES, 0.0)
                )
                role_scores.append(
                    [1.0 if r in titled else predicted[r] for r in ROLES]
                )
                degrees.append(degrees_named(title) or NONE_NAMED)
                terms.append(terms_named(title) or NONE_NAMED)
            await asyncio.sleep(0)
    return PostingIndex(
        job_ids=np.array(job_ids, dtype=np.int64),
        company_ids=np.array(company_ids, dtype=np.int64),
        groups=np.array(groups, dtype=np.int32),
        rows=np.repeat(np.arange(len(job_ids), dtype=np.int32), np.diff(starts)),
        columns=np.concatenate(columns) if columns else np.empty(0, dtype=np.int32),
        values=np.concatenate(values) if values else np.empty(0, dtype=np.float32),
        starts=np.array(starts),
        role_scores=np.array(role_scores, dtype=np.float32).reshape(-1, len(ROLES)),
        degrees=degrees,
        terms=terms,
        words=list(model.vocabulary),
    )


class IndexCache:
    """Keeps one index per kind of posting, rebuilt after new ingestion runs."""

    def __init__(self, pool: AsyncConnectionPool, model: RoleModel) -> None:
        self.pool = pool
        self.model = model
        self._indexes: dict[bool, tuple[int, float, PostingIndex]] = {}
        self._lock = asyncio.Lock()

    async def get(self, internships: bool) -> PostingIndex:
        async with self._lock:
            run_id, checked_at, index = self._indexes.get(internships, (-1, 0.0, None))
            if index is not None and time.monotonic() - checked_at < REFRESH_SECONDS:
                return index
            latest = await latest_run(self.pool)
            if index is None or latest != run_id:
                index = await build_index(self.pool, self.model, internships)
            self._indexes[internships] = (latest, time.monotonic(), index)
            return index


async def latest_run(pool: AsyncConnectionPool) -> int:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT coalesce(max(id), 0) FROM ingestion_runs WHERE finished_at IS NOT NULL"
        )
        return (await cursor.fetchone())[0]
