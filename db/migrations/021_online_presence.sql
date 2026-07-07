-- Per-app-instance presence for accurate online counts (one row per browser tab).

CREATE TABLE IF NOT EXISTS public.online_presence (
    instance_id TEXT PRIMARY KEY,
    user_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_online_presence_last_seen
    ON public.online_presence(last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_online_presence_user_id
    ON public.online_presence(user_id)
    WHERE user_id IS NOT NULL;
