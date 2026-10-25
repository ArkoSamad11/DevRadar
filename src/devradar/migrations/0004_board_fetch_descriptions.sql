ALTER TABLE board_fetches
    ADD COLUMN unchanged_count      integer NOT NULL DEFAULT 0,
    ADD COLUMN reopened_count       integer NOT NULL DEFAULT 0,
    ADD COLUMN descriptions_fetched integer NOT NULL DEFAULT 0,
    ADD COLUMN descriptions_failed  integer NOT NULL DEFAULT 0;
