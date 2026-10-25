CREATE TABLE companies (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text NOT NULL,
    slug        text NOT NULL UNIQUE,
    website     text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE job_boards (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id            bigint NOT NULL REFERENCES companies (id),
    ats                   text NOT NULL CHECK (ats IN ('greenhouse', 'lever', 'ashby', 'workday')),
    board_token           text NOT NULL,
    discovered_via        text NOT NULL,
    enabled               boolean NOT NULL DEFAULT true,
    consecutive_failures  integer NOT NULL DEFAULT 0,
    last_success_at       timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ats, board_token)
);

CREATE TABLE jobs (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    posting_key    text NOT NULL UNIQUE,
    company_id     bigint NOT NULL REFERENCES companies (id),
    title          text NOT NULL,
    url            text NOT NULL,
    description    text,
    is_internship  boolean NOT NULL,
    status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    posted_at      timestamptz,
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    closed_at      timestamptz,
    CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);

CREATE TABLE job_locations (
    job_id     bigint NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    raw        text NOT NULL,
    is_remote  boolean NOT NULL DEFAULT false,
    PRIMARY KEY (job_id, raw)
);

CREATE TABLE job_sources (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id         bigint NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    job_board_id   bigint REFERENCES job_boards (id),
    source         text NOT NULL CHECK (source IN ('greenhouse', 'lever', 'ashby', 'workday', 'vansh')),
    external_id    text NOT NULL,
    url            text NOT NULL,
    raw            jsonb NOT NULL,
    content_hash   text NOT NULL,
    missed_fetches integer NOT NULL DEFAULT 0,
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, external_id)
);

CREATE INDEX job_sources_job_id_idx ON job_sources (job_id);

CREATE TABLE categories (
    id    smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug  text NOT NULL UNIQUE,
    name  text NOT NULL
);

CREATE TABLE job_categories (
    job_id       bigint NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    category_id  smallint NOT NULL REFERENCES categories (id),
    PRIMARY KEY (job_id, category_id)
);

INSERT INTO categories (slug, name) VALUES
    ('software', 'Software Engineering'),
    ('ml_ai', 'Machine Learning & AI'),
    ('data_science', 'Data Science'),
    ('systems', 'Systems & Infrastructure'),
    ('quant', 'Quantitative Trading & Research'),
    ('operations_research', 'Operations Research'),
    ('math', 'Mathematics & Statistics');

CREATE TABLE ingestion_runs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trigger      text NOT NULL CHECK (trigger IN ('schedule', 'manual')),
    status       text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);

CREATE TABLE board_fetches (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id          bigint NOT NULL REFERENCES ingestion_runs (id),
    job_board_id    bigint NOT NULL REFERENCES job_boards (id),
    status          text NOT NULL CHECK (status IN ('succeeded', 'failed')),
    attempts        smallint NOT NULL,
    fetched_count   integer NOT NULL DEFAULT 0,
    inserted_count  integer NOT NULL DEFAULT 0,
    updated_count   integer NOT NULL DEFAULT 0,
    closed_count    integer NOT NULL DEFAULT 0,
    rejected_count  integer NOT NULL DEFAULT 0,
    duration_ms     integer NOT NULL,
    error           text,
    UNIQUE (run_id, job_board_id)
);

-- Search indexes (title/location/category filters) are intentionally absent:
-- the benchmark measures this schema as the baseline before adding them.
