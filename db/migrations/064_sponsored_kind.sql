-- 064: Sponsored placements share the boost tables; `kind` separates the two paid queues.

ALTER TABLE public.boost_plans
    ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'boost';
ALTER TABLE public.listing_boosts
    ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'boost';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'boost_plans_kind_check') THEN
        ALTER TABLE public.boost_plans
            ADD CONSTRAINT boost_plans_kind_check CHECK (kind IN ('boost', 'sponsored'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'listing_boosts_kind_check') THEN
        ALTER TABLE public.listing_boosts
            ADD CONSTRAINT listing_boosts_kind_check CHECK (kind IN ('boost', 'sponsored'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_listing_boosts_kind_active
    ON public.listing_boosts (kind, active, ends_at DESC);

INSERT INTO public.boost_plans (name, duration_hours, price_amount, score_bonus, is_active, kind)
SELECT 'Sponsored 7 days', 168, 25000, 50, false, 'sponsored'
WHERE NOT EXISTS (SELECT 1 FROM public.boost_plans WHERE kind = 'sponsored');
