CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ILIKE spends most of its time folding case, so the folded text is stored once.
ALTER TABLE jobs
    ADD COLUMN search_text text
    GENERATED ALWAYS AS (lower(title || E'\n' || coalesce(description, ''))) STORED;

CREATE INDEX jobs_active_search_trgm_idx ON jobs USING gin (search_text gin_trgm_ops)
    WHERE status = 'active';
CREATE INDEX jobs_active_listed_idx ON jobs (listed_at DESC, id DESC) WHERE status = 'active';
CREATE INDEX jobs_active_internships_listed_idx ON jobs (listed_at DESC, id DESC)
    WHERE status = 'active' AND is_internship;
CREATE INDEX jobs_active_company_idx ON jobs (company_id) INCLUDE (is_internship)
    WHERE status = 'active';
