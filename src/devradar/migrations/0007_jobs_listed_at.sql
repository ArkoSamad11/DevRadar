ALTER TABLE jobs
    ADD COLUMN listed_at timestamptz GENERATED ALWAYS AS (coalesce(posted_at, first_seen_at)) STORED;
