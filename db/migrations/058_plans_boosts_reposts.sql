-- 058: admin-editable plans, boost allowances, reposts.
-- Code values in payments/plans.py stay the fallback when this table is missing.

CREATE TABLE IF NOT EXISTS public.plans (
    key                         text PRIMARY KEY,
    name                        text NOT NULL,
    price_ugx                   integer NOT NULL DEFAULT 0 CHECK (price_ugx >= 0),
    currency                    text NOT NULL DEFAULT 'UGX',
    billing_period_days         integer NOT NULL DEFAULT 30 CHECK (billing_period_days > 0),
    max_shops                   integer NOT NULL DEFAULT 1 CHECK (max_shops >= 0),
    max_products_per_shop       integer NOT NULL DEFAULT 10 CHECK (max_products_per_shop >= 0),
    analytics_enabled           boolean NOT NULL DEFAULT false,
    frequency_per_day           integer NOT NULL DEFAULT 0 CHECK (frequency_per_day >= 0),
    frequency_per_day_boosted   integer NOT NULL DEFAULT 0 CHECK (frequency_per_day_boosted >= 0),
    max_photos                  integer CHECK (max_photos BETWEEN 1 AND 20),
    max_videos                  integer CHECK (max_videos BETWEEN 0 AND 5),
    free_boosts_per_month       integer NOT NULL DEFAULT 0 CHECK (free_boosts_per_month >= 0),
    reposts_per_day             integer NOT NULL DEFAULT 1 CHECK (reposts_per_day >= 0),
    repost_cooldown_hours       integer NOT NULL DEFAULT 24 CHECK (repost_cooldown_hours >= 0),
    features                    jsonb NOT NULL DEFAULT '[]'::jsonb,
    sort_order                  integer NOT NULL DEFAULT 0,
    is_active                   boolean NOT NULL DEFAULT true,
    updated_at                  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.plans ENABLE ROW LEVEL SECURITY;
GRANT ALL ON public.plans TO service_role;

INSERT INTO public.plans (key, name, price_ugx, billing_period_days, max_shops, max_products_per_shop,
    analytics_enabled, frequency_per_day, frequency_per_day_boosted, max_photos, max_videos,
    free_boosts_per_month, reposts_per_day, repost_cooldown_hours, sort_order)
VALUES
    ('basic',    'Basic',        0, 30,  1, 10, false, 1, 0, 3, 1, 0, 1, 24, 0),
    ('standard', 'Standard', 30000, 30,  3, 20, true,  0, 2, 3, 1, 2, 3, 12, 1),
    ('premium',  'Premium',  80000, 30, 10, 50, true,  0, 5, 3, 1, 5, 5,  6, 2)
ON CONFLICT (key) DO NOTHING;

-- Boosts: score_bonus is now the literal feed points added. Keep old behaviour (<=25 -> 50, >25 -> 75).
UPDATE public.boost_plans SET score_bonus = CASE WHEN score_bonus > 25 THEN 75 ELSE 50 END
WHERE score_bonus <= 25 OR (score_bonus > 25 AND score_bonus < 75);
INSERT INTO public.boost_plans (name, duration_hours, price_amount, score_bonus, is_active)
SELECT 'Boost 7 days', 168, 10000, 50, true
WHERE NOT EXISTS (SELECT 1 FROM public.boost_plans);

ALTER TABLE public.listing_boosts
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'paid';
CREATE INDEX IF NOT EXISTS idx_listing_boosts_seller_source
    ON public.listing_boosts (seller_id, source, created_at DESC);

-- Reposts: created_at moves to the repost time; first_posted_at keeps the original.
ALTER TABLE public.products ADD COLUMN IF NOT EXISTS first_posted_at timestamptz;
ALTER TABLE public.products ADD COLUMN IF NOT EXISTS repost_count integer NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS public.listing_reposts (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id  uuid NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_listing_reposts_user ON public.listing_reposts (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_listing_reposts_listing ON public.listing_reposts (listing_id, created_at DESC);
ALTER TABLE public.listing_reposts ENABLE ROW LEVEL SECURITY;
GRANT ALL ON public.listing_reposts TO service_role;
GRANT ALL ON public.boost_plans, public.listing_boosts TO service_role;
