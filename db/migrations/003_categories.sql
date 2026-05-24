-- =============================================================================
-- 003_categories.sql — canonical browse categories (seeded, read-only via API)
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.categories (
    slug TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.categories ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "categories_select_public" ON public.categories;
CREATE POLICY "categories_select_public"
  ON public.categories
  FOR SELECT
  TO anon, authenticated
  USING (true);

INSERT INTO public.categories (slug, label, sort_order) VALUES
  ('food-beverage', 'Food & Beverage', 1),
  ('fashion', 'Fashion', 2),
  ('electronics', 'Electronics', 3),
  ('beauty', 'Beauty', 4),
  ('home-living', 'Home & Living', 5),
  ('services', 'Services', 6),
  ('agriculture', 'Agriculture', 7),
  ('health-wellness', 'Health & Wellness', 8),
  ('sports-outdoors', 'Sports & Outdoors', 9),
  ('automotive', 'Automotive', 10),
  ('books-stationery', 'Books & Stationery', 11),
  ('kids-baby', 'Kids & Baby', 12),
  ('pets', 'Pets', 13),
  ('other', 'Other', 99)
ON CONFLICT (slug) DO UPDATE SET
  label = EXCLUDED.label,
  sort_order = EXCLUDED.sort_order;
