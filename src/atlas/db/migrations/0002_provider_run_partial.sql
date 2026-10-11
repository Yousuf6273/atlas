-- Provider runs that returned only part of their coverage (e.g. map tiles that failed because public
-- servers were overloaded) are re-run on resume instead of being treated as complete.
ALTER TABLE provider_runs ADD COLUMN partial INTEGER NOT NULL DEFAULT 0 CHECK (partial IN (0, 1));
