-- Rewriting every seen row each run made the table grow by its full size per run (MVCC).
-- A source with missed_fetches = 0 was last seen at its board's last_success_at.
ALTER TABLE job_sources DROP COLUMN last_seen_at;
