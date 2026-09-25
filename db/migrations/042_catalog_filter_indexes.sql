-- Indexes for server-side home/browse/search filters.
-- Safe to apply after 041. Not required for correctness; keeps filtered
-- pagination off sequential scans as the catalog grows.

-- Approved Phase 3 badges copied onto shops.trust_badges.
CREATE INDEX IF NOT EXISTS idx_shops_trust_badges_gin
  ON public.shops USING gin (trust_badges);

-- shop_verifications.metadata stage status (migration 041).
CREATE INDEX IF NOT EXISTS idx_shop_verifications_stage2_verified
  ON public.shop_verifications ((metadata->>'stage2_status'))
  WHERE metadata->>'stage2_status' = 'verified';

CREATE INDEX IF NOT EXISTS idx_shop_verifications_stage3_verified
  ON public.shop_verifications ((metadata->>'stage3_status'))
  WHERE metadata->>'stage3_status' = 'verified';

CREATE INDEX IF NOT EXISTS idx_shop_verifications_stage4_verified
  ON public.shop_verifications ((metadata->>'stage4_status'))
  WHERE metadata->>'stage4_status' = 'verified';

-- Price and listing-type filters on the public catalog.
CREATE INDEX IF NOT EXISTS idx_products_active_price
  ON public.products (price_ugx, created_at DESC)
  WHERE status = 'active' AND is_published = true;

CREATE INDEX IF NOT EXISTS idx_products_active_item_type_created
  ON public.products (item_type, created_at DESC)
  WHERE status = 'active' AND is_published = true;

-- Near-me reads shops.location lat/lng. Expression index so the JSON
-- lookup is not a sequential scan. Distance is still computed in the app
-- over the located rows (no PostGIS).
CREATE INDEX IF NOT EXISTS idx_shops_location_lat
  ON public.shops (((location->>'lat')))
  WHERE location IS NOT NULL;
