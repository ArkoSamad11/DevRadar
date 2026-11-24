ALTER TABLE job_boards ADD COLUMN disabled_at timestamptz;

UPDATE job_boards SET disabled_at = now(), etag = NULL WHERE NOT enabled;

UPDATE jobs j SET status = 'closed', closed_at = now()
WHERE j.status = 'active'
  AND EXISTS (SELECT 1 FROM job_sources s WHERE s.job_id = j.id)
  AND NOT EXISTS (
      SELECT 1 FROM job_sources s
      LEFT JOIN job_boards b ON b.id = s.job_board_id
      WHERE s.job_id = j.id AND (b.id IS NULL OR b.enabled)
  );
