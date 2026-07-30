-- Listing type expansion + flexible type-specific metadata ("more information").
-- item_type remains the discriminator; listing_meta holds per-type fields we can extend
-- without more ALTER TABLE migrations (FB Marketplace / Etsy attribute pattern).

ALTER TABLE public.products
  DROP CONSTRAINT IF EXISTS products_item_type_check;

ALTER TABLE public.products
  ADD CONSTRAINT products_item_type_check
  CHECK (item_type IN ('product', 'service', 'property', 'job', 'opportunity'));

ALTER TABLE public.products
  ADD COLUMN IF NOT EXISTS listing_meta JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.products.listing_meta IS
  'Type-specific more-information JSON (condition, pricing_model, opportunity_kind, etc.)';

CREATE INDEX IF NOT EXISTS idx_products_listing_meta
  ON public.products USING gin (listing_meta);

CREATE INDEX IF NOT EXISTS idx_products_item_type
  ON public.products (item_type);
