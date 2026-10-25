CREATE TABLE link_resolutions (
    short_url     text PRIMARY KEY,
    resolved_url  text,
    status_code   smallint NOT NULL,
    resolved_at   timestamptz NOT NULL DEFAULT now()
);
