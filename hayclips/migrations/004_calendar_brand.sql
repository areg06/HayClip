-- Phase 1d: local planning calendar (planning only: nothing is ever published from here) and Brand Kit.
CREATE TABLE calendar_items (
    id          bigserial PRIMARY KEY,
    project_id  text NOT NULL REFERENCES projects(id),
    clip_id     text NOT NULL,
    plan_date   date NOT NULL,
    plan_time   time,
    platforms   text[] NOT NULL DEFAULT '{}',   -- subset of {tiktok, reels, shorts}
    note        text,
    status      text NOT NULL DEFAULT 'Draft' CHECK (status IN ('Draft', 'Editing', 'Ready', 'Scheduled')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX calendar_items_date ON calendar_items (plan_date);

-- one row: the local brand defaults applied to newly chosen clips
CREATE TABLE brand_kit (
    id          integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    settings    jsonb NOT NULL DEFAULT '{}',
    logo_file   text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
