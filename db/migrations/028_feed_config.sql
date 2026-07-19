-- ============================================================================
-- MIGRATION 028 — feed_config
-- ============================================================================
-- Admin-configurable overrides for the feed scoring/placement engine
-- (`midoraapi/feed/config.py`). A single row keyed by `key = 'default'`
-- holds a JSONB blob of tunables; feed.config.load_overrides() merges this
-- on top of the code defaults.
--
-- The overrides only accept a whitelisted key set (enforced in Python) so
-- misconfiguration cannot inject unexpected fields.

CREATE TABLE IF NOT EXISTS public.feed_config (
    key         TEXT PRIMARY KEY,
    overrides   JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  UUID REFERENCES public.users(id) ON DELETE SET NULL
);

ALTER TABLE public.feed_config ENABLE ROW LEVEL SECURITY;

-- Only service_role writes; reads go through the admin service too.
GRANT SELECT, INSERT, UPDATE ON public.feed_config TO service_role;

-- Seed the default row (empty overrides = use code defaults).
INSERT INTO public.feed_config (key, overrides)
VALUES ('default', '{}'::jsonb)
ON CONFLICT (key) DO NOTHING;
