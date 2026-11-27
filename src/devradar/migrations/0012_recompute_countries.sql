-- The country rules changed; the next ingestion run works countries out again.
UPDATE jobs SET countries = NULL;
