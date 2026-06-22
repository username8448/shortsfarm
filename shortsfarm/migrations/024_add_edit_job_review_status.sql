-- 024: Human review state for rendered template edit jobs
ALTER TABLE edit_jobs
    ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK(review_status IN ('pending', 'approved', 'rejected'));

ALTER TABLE edit_jobs
    ADD COLUMN reviewed_at TEXT;

ALTER TABLE edit_jobs
    ADD COLUMN review_note TEXT;

CREATE INDEX IF NOT EXISTS idx_edit_jobs_review_status
    ON edit_jobs(review_status);
