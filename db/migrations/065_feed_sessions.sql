CREATE TABLE IF NOT EXISTS public.feed_sessions (
    session_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feed_sessions_updated ON public.feed_sessions (updated_at);

ALTER TABLE public.feed_sessions ENABLE ROW LEVEL SECURITY;
