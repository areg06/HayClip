-- Phase 1c: a project whose local folder disappeared stays in the index (state MISSING_STORAGE,
-- computed from the filesystem) until an operator explicitly removes the stale entry. Removal is a
-- soft delete: the row, its jobs and its audit events are kept.
ALTER TABLE projects ADD COLUMN removed_at timestamptz;
ALTER TABLE projects ADD COLUMN removed_by text;
