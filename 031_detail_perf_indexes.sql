-- Product + shop detail page indexes.
-- Targets: shop product grids, reviews, comments, likes, listing events.

-- Public shop inventory (list_products ordered by created_at)
CREATE INDEX IF NOT EXISTS idx_products_shop_published_created
  ON public.products (shop_id, created_at DESC)
  WHERE status = 'active' AND is_published = true;

-- Owner/admin shop inventory (all statuses)
CREATE INDEX IF NOT EXISTS idx_products_shop_created
  ON public.products (shop_id, created_at DESC);

-- Product reviews list + stats
CREATE INDEX IF NOT EXISTS idx_product_reviews_product_created
  ON public.product_reviews (product_id, created_at DESC);

-- Product comments (public list hides flagged)
CREATE INDEX IF NOT EXISTS idx_product_comments_product_flagged_created
  ON public.product_comments (product_id, created_at DESC)
  WHERE is_flagged = false;

-- Seller / shop reviews tab
CREATE INDEX IF NOT EXISTS idx_seller_reviews_seller_created
  ON public.seller_reviews (seller_id, created_at DESC);

-- Viewer like lookup on PDP
CREATE INDEX IF NOT EXISTS idx_product_likes_product_user
  ON public.product_likes (product_id, user_id);

-- Shop follows / likes viewer checks
CREATE INDEX IF NOT EXISTS idx_shop_follows_shop_user
  ON public.shop_follows (shop_id, user_id);

CREATE INDEX IF NOT EXISTS idx_shop_likes_shop_user
  ON public.shop_likes (shop_id, user_id);

-- Product listing events by type (PDP whatsapp/message counts)
CREATE INDEX IF NOT EXISTS idx_listing_events_listing_type
  ON public.listing_events (listing_id, event_type);

-- Shop-level events (no listing_id)
CREATE INDEX IF NOT EXISTS idx_listing_events_seller_type_nolisting
  ON public.listing_events (seller_id, event_type)
  WHERE listing_id IS NULL;
