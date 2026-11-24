# Search and listing benchmark

Latency in milliseconds of the HTTP API before and after migration `0010_search_indexes`, measured
on 2026-11-24. Raw results: [benchmark-results.json](benchmark-results.json).

## One request at a time

| Request | p50 before | p50 after | p95 before | p95 after |
|---|---:|---:|---:|---:|
| newest internships | 5.6 | 2.1 | 6.5 | 2.9 |
| newest, all jobs | 12.4 | 1.7 | 15.1 | 3.1 |
| page 2000 by cursor | 8.3 | 1.7 | 10.9 | 3.1 |
| category | 6.2 | 2.8 | 10.4 | 4.5 |
| keyword, common | 117.9 | 15.3 | 130.2 | 21.2 |
| keyword, rare | 117.6 | 3.6 | 129.9 | 4.6 |
| keyword, no match | 115.1 | 1.3 | 129.9 | 2.2 |
| keyword, all jobs | 132.9 | 3.7 | 148.3 | 4.4 |
| location | 13.7 | 5.2 | 28.4 | 25.9 |
| remote | 12.2 | 2.1 | 14.8 | 3.9 |
| keyword + category + location | 15.9 | 20.9 | 19.4 | 24.6 |
| keyword + broad filters, all jobs | 130.5 | 4.6 | 140.3 | 22.4 |
| keyword, including closed | 152.7 | 64.5 | 168.1 | 69.7 |
| one company's jobs | 5.0 | 1.6 | 6.0 | 2.4 |
| job detail | 1.1 | 1.1 | 1.8 | 1.4 |
| companies hiring interns | 305.9 | 6.8 | 366.9 | 8.1 |
| companies | 294.7 | 5.5 | 324.3 | 6.4 |
| sources | 47.5 | 48.9 | 54.0 | 62.1 |

## Eight clients at once

| Request | p50 before | p50 after | p95 before | p95 after |
|---|---:|---:|---:|---:|
| newest internships | 8.2 | 4.8 | 14.4 | 12.1 |
| newest, all jobs | 15.9 | 4.4 | 23.2 | 12.3 |
| page 2000 by cursor | 9.8 | 4.2 | 15.0 | 14.0 |
| category | 10.9 | 4.6 | 23.3 | 9.7 |
| keyword, common | 134.8 | 32.3 | 147.6 | 55.0 |
| keyword, rare | 132.3 | 5.2 | 149.8 | 11.1 |
| keyword, no match | 135.9 | 4.2 | 154.5 | 14.0 |
| keyword, all jobs | 184.0 | 5.2 | 202.8 | 11.5 |
| location | 15.9 | 6.2 | 29.1 | 29.8 |
| remote | 16.1 | 4.6 | 23.2 | 12.7 |
| keyword + category + location | 22.4 | 49.6 | 29.3 | 59.0 |
| keyword + broad filters, all jobs | 176.2 | 6.6 | 190.2 | 49.4 |
| keyword, including closed | 221.2 | 157.4 | 231.9 | 183.7 |
| one company's jobs | 6.2 | 4.2 | 11.2 | 13.1 |
| job detail | 3.8 | 3.9 | 16.3 | 14.4 |
| companies hiring interns | 368.7 | 7.9 | 394.8 | 13.8 |
| companies | 361.1 | 6.4 | 384.1 | 12.0 |
| sources | 96.3 | 91.1 | 132.7 | 137.4 |

## What changed

- **Companies:** counting a company's jobs scanned all 80,038 jobs, about 102 times per page.
  A partial covering index on `company_id` answers the counts without reading the table.
- **Keyword search:** `ILIKE` unpacked and case-folded every internship description. The folded text
  is stored once in `search_text` with a trigram index. Results are unchanged.
- **Listings:** partial indexes on `(listed_at, id)` return the newest rows without sorting.
- **Plan caching off:** after a connection ran common searches, Postgres kept one plan for all
  searches, and in a controlled test 80 of 80 no-match searches were then slow, against 0 of 80 with
  `plan_cache_mode = force_custom_plan`.

## What got worse or did not improve

- **Keyword + category + location is slower:** 15.9 to 20.9 ms p50 alone, 22.4 to 49.6 ms with eight
  clients. Postgres underestimates the cost of matching long text and does it before the narrow
  filters. Forcing the filters first fixed this case but made broad searches slower (76 to 174 ms),
  so it was dropped.
- **Searches including closed jobs** are not covered by the partial indexes and stay slow.
- **`/sources` and job detail** are unchanged.

## Cost

| | Before | After |
|---|---:|---:|
| Database size | 150 MB | 186 MB |
| Insert 1,000 postings | 997 ms, 1.5 MB WAL | 1,164 ms, 3.5 MB WAL |
| Re-store 1,000 unchanged postings | 21 ms | 23 ms |
| Update 1,000 postings | 1,041 ms, 1.8 MB WAL | 1,222 ms, 6.9 MB WAL |

## Method

- **Data:** a fixed copy of the real database: 80,038 jobs, 60,222 active, 3,491 active internships.
- **Hardware:** Apple M5 Pro, 15 cores, 24 GB; Postgres 16 in Docker with default settings and data
  in memory; one uvicorn process; load generator on the same machine.
- **Load:** closed loop, 240 requests per cell (4 rounds of 60) after warm-up, alternating which side
  runs first. "Before" runs the code at commit `a45fb54` on an unindexed copy.
- **Correctness:** the run aborts unless both sides return identical JSON; 130 URLs matched.
- **Noise:** with identical databases on both sides, p50 differed by up to about 7% and p95 by up to
  about 20%. Smaller differences should be ignored.
- **Limits:** one machine, no network, a warm cache, and a hand-written request mix that does not
  reflect real traffic. Repeating the same few URLs favours caching on both sides.

## Alternatives rejected

- **Full-text search (`tsvector`)** was faster still, but changes results: `c#` and `c++` become the
  same word, and `verilog` misses `SystemVerilog`.
- **Trigram indexes on `title` and `description`** used 16 MB less, but common keywords took about
  three times as long, since every candidate is case-folded again (46 vs 16 ms in SQL).
- **Indexes on locations and categories** showed no gain on top of the other indexes.

## Reproduce

```bash
git worktree add /tmp/devradar-before a45fb54
uv run python scripts/benchmark.py BEFORE_DB AFTER_DB --before-src /tmp/devradar-before/src
uv run python scripts/benchmark_writes.py BEFORE_DB AFTER_DB
```
