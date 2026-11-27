-- Countries live apart from jobs: updating a jobs row rewrites its generated search_text, which
-- for every posting at once is more than the database volume holds.
CREATE TABLE job_countries (
    job_id   bigint NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    country  text NOT NULL,
    PRIMARY KEY (job_id, country)
);
