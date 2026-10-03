-- Phase 1c: what the operator actually ended up with and how much they had to change, captured at
-- decision time from real actions only (edit events, manual trim, crop plan changes). NULL = unknown.
ALTER TABLE review_decisions ADD COLUMN final_title text;
ALTER TABLE review_decisions ADD COLUMN final_hook text;
ALTER TABLE review_decisions ADD COLUMN final_trim jsonb;            -- null = automatic sentence snap
ALTER TABLE review_decisions ADD COLUMN warning_count integer;       -- CHECK lines on the latest render
ALTER TABLE review_decisions ADD COLUMN title_edited boolean;
ALTER TABLE review_decisions ADD COLUMN hook_edited boolean;
ALTER TABLE review_decisions ADD COLUMN trim_edited boolean;
ALTER TABLE review_decisions ADD COLUMN framing_adjusted boolean;    -- crop.json differs from the automatic plan
ALTER TABLE review_decisions ADD COLUMN transcript_edited boolean;   -- not supported yet: always NULL
