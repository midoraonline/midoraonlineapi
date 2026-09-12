-- Phase 37: Shop plans (basic/standard/premium) — configurable limits live in
-- midoraapi/payments/plans.py. This migration only adds the columns needed to
-- track which plan a merchant is on and when it expires.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS plan_tier TEXT NOT NULL DEFAULT 'basic'
        CHECK (plan_tier IN ('basic', 'standard', 'premium'));

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS plan_expires_at TIMESTAMPTZ;

ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS plan_tier TEXT NOT NULL DEFAULT 'basic'
        CHECK (plan_tier IN ('basic', 'standard', 'premium'));

CREATE INDEX IF NOT EXISTS idx_users_plan_tier ON public.users(plan_tier);
