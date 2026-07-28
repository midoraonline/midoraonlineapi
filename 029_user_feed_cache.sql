-- Per-user feed cache (run manually in Supabase SQL editor or migration tool)
CREATE TABLE IF NOT EXISTS public.user_feed_cache (
    user_id       UUID PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
    ranked_ids    JSONB NOT NULL DEFAULT '[]'::jsonb,
    refreshed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.user_feed_cache ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_feed_cache TO service_role;

CREATE INDEX IF NOT EXISTS idx_user_feed_cache_refreshed_at
    ON public.user_feed_cache(refreshed_at DESC);
