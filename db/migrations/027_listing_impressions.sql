-- ============================================================================
-- MIGRATION 027 — listing_impressions
-- ============================================================================
-- Records every time a listing is actually rendered on a buyer's screen
-- (visible >= 50% for at least 1s). Separate from `listing_events` so the
-- high-volume impression stream doesn't bloat the mixed-event table.
--
-- Feeds two downstream systems:
--   1. Seller-facing reporting (billboard impressions per pool).
--   2. Feed ranking — per-shop exposure multiplier + per-buyer fatigue
--      suppression.
--
-- `pool` records which layer the listing was surfaced from:
--   organic | boosted | sponsored | super_boost | premium_store | fresh |
--   exploration
--
-- Anonymous impressions use `session_id` + `device_hash` in place of buyer_id.

CREATE TABLE IF NOT EXISTS public.listing_impressions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id   UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    buyer_id     UUID REFERENCES public.users(id) ON DELETE SET NULL,
    session_id   TEXT,
    device_hash  TEXT,
    pool         TEXT NOT NULL DEFAULT 'organic'
                     CHECK (pool IN (
                        'organic', 'boosted', 'sponsored', 'super_boost',
                        'premium_store', 'fresh', 'exploration'
                     )),
    position     INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.listing_impressions ENABLE ROW LEVEL SECURITY;

-- Seller reporting: "impressions for my listings in the last N days"
CREATE INDEX IF NOT EXISTS idx_listing_impressions_listing_time
    ON public.listing_impressions (listing_id, created_at DESC);

-- Feed fatigue: "listings this buyer has seen recently"
CREATE INDEX IF NOT EXISTS idx_listing_impressions_buyer_time
    ON public.listing_impressions (buyer_id, created_at DESC)
    WHERE buyer_id IS NOT NULL;

-- Anonymous fatigue: "listings this session has seen recently"
CREATE INDEX IF NOT EXISTS idx_listing_impressions_session_time
    ON public.listing_impressions (session_id, created_at DESC)
    WHERE session_id IS NOT NULL;

-- Exposure multiplier: "impressions per shop in the last 24h"
-- (Join through products.shop_id — cheap because listing_id is indexed above.)


-- ============================================================================
-- Aggregate view — one row per listing, split by pool
-- ============================================================================
-- Used by seller dashboards. Materialise as a view (cheap) rather than
-- maintaining counters on `products` (which would need locks).

CREATE OR REPLACE VIEW public.v_listing_impressions_agg AS
SELECT
    listing_id,
    COUNT(*)                                                       AS total,
    COUNT(*) FILTER (WHERE pool = 'organic')                       AS organic,
    COUNT(*) FILTER (WHERE pool = 'boosted')                       AS boosted,
    COUNT(*) FILTER (WHERE pool = 'sponsored')                     AS sponsored,
    COUNT(*) FILTER (WHERE pool = 'super_boost')                   AS super_boost,
    COUNT(*) FILTER (WHERE pool = 'premium_store')                 AS premium_store,
    COUNT(*) FILTER (WHERE pool = 'fresh')                         AS fresh,
    COUNT(*) FILTER (WHERE pool = 'exploration')                   AS exploration,
    MAX(created_at)                                                AS last_impression_at
FROM public.listing_impressions
GROUP BY listing_id;

GRANT SELECT ON public.v_listing_impressions_agg TO authenticated, service_role;
