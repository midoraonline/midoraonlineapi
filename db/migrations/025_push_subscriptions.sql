-- =============================================================================
-- 025_push_subscriptions.sql
--
-- Stores browser Web Push subscriptions so FastAPI can deliver push
-- notifications (VAPID) to each user's registered devices on new messages,
-- notifications, etc.
--
-- One user can have many subscriptions (multi-device / multi-browser).
-- Duplicate endpoints are collapsed via unique index.
--
-- Idempotent: safe to re-run.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.push_subscriptions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    endpoint      TEXT NOT NULL,
    p256dh        TEXT NOT NULL,
    auth          TEXT NOT NULL,
    user_agent    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.push_subscriptions ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX IF NOT EXISTS idx_push_subs_endpoint
    ON public.push_subscriptions(endpoint);
CREATE INDEX IF NOT EXISTS idx_push_subs_user_id
    ON public.push_subscriptions(user_id);

-- Only the service role writes/reads this table; RLS stays fully closed to
-- both anon and authenticated roles.
