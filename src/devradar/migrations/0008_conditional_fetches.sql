ALTER TABLE job_boards ADD COLUMN etag text;
ALTER TABLE board_fetches ADD COLUMN not_modified boolean NOT NULL DEFAULT false;
