-- Feed hot-path indexes.
-- Guest/latest, trending, premium, and candidate-pool queries all filter
-- status=active + is_published=true and sort by created_at / view_count /
-- listing_score. Partial composites keep these index-only and small.

CREATE INDEX IF NOT EXISTS idx_products_feed_latest
  ON public.products (created_at DESC)
  WHERE status = 'active' AND is_published = true;

CREATE INDEX IF NOT EXISTS idx_products_feed_trending
  ON public.products (view_count DESC, created_at DESC)
  WHERE status = 'active' AND is_published = true;

CREATE INDEX IF NOT EXISTS idx_products_feed_premium
  ON public.products (listing_score DESC, created_at DESC)
  WHERE status = 'active' AND is_published = true;

CREATE INDEX IF NOT EXISTS idx_products_feed_shop_score
  ON public.products (shop_id, listing_score DESC)
  WHERE status = 'active' AND is_published = true;

CREATE INDEX IF NOT EXISTS idx_products_feed_category_score
  ON public.products (category, listing_score DESC)
  WHERE status = 'active' AND is_published = true;

-- Active boost lookups used during enrichment / scoring.
CREATE INDEX IF NOT EXISTS idx_listing_boosts_active_ends
  ON public.listing_boosts (listing_id, ends_at DESC)
  WHERE active = true;

-- Like / review enrichment by product_id batches.
CREATE INDEX IF NOT EXISTS idx_product_likes_product_id
  ON public.product_likes (product_id);

CREATE INDEX IF NOT EXISTS idx_product_reviews_product_id
  ON public.product_reviews (product_id);

-- Feed cache freshness scans.
CREATE INDEX IF NOT EXISTS idx_user_feed_cache_refreshed
  ON public.user_feed_cache (refreshed_at DESC);
