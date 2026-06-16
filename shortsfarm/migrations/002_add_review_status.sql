-- 002: Add review_status column to videos
ALTER TABLE videos ADD COLUMN review_status TEXT NOT NULL DEFAULT 'inbox';
