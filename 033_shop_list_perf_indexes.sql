-- Shop directory list performance (mirrors 030 feed patterns).
-- Apply in Supabase SQL editor.

-- Public directory: active shops by recency (matches list_shops default sort)
CREATE INDEX IF NOT EXISTS idx_shops_active_created
  ON public.shops (created_at DESC)
  WHERE is_active = true;

-- Filter by shop_type + sort
CREATE INDEX IF NOT EXISTS idx_shops_active_type_created
  ON public.shops (shop_type, created_at DESC)
  WHERE is_active = true;

-- Batch product-category map for shop cards / filters
CREATE INDEX IF NOT EXISTS idx_products_shop_category_published
  ON public.products (shop_id, category)
  WHERE status = 'active' AND is_published = true AND category IS NOT NULL;
