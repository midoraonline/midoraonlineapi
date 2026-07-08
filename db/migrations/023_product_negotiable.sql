-- Add negotiable price flag to product listings
ALTER TABLE public.products
  ADD COLUMN IF NOT EXISTS is_negotiable BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN public.products.is_negotiable IS 'Whether the listed price is open to negotiation';
