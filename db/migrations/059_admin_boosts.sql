-- 059: admin-granted boosts (testing). No payment, no plan allowance.
-- Custom boosts may have no package; business boosts tag each listing row with the shop.
ALTER TABLE public.listing_boosts ALTER COLUMN boost_plan_id DROP NOT NULL;
ALTER TABLE public.listing_boosts
    ADD COLUMN IF NOT EXISTS granted_by uuid REFERENCES public.users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS shop_id uuid REFERENCES public.shops(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS ended_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_listing_boosts_active_ends
    ON public.listing_boosts (active, ends_at DESC);
CREATE INDEX IF NOT EXISTS idx_listing_boosts_shop
    ON public.listing_boosts (shop_id) WHERE shop_id IS NOT NULL;
