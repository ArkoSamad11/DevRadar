-- Lever's EU boards live on a separate API (api.eu.lever.co) with its own token namespace.
ALTER TABLE job_boards
    ADD COLUMN api_region text NOT NULL DEFAULT 'global' CHECK (api_region IN ('global', 'eu'));

ALTER TABLE job_boards DROP CONSTRAINT job_boards_ats_board_token_key;
ALTER TABLE job_boards ADD UNIQUE (ats, api_region, board_token);
