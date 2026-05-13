-- Adds optional storefront category for public shop listing / browse filters.
-- Safe to run once on existing databases that were created from an older schema.
ALTER TABLE public.shops ADD COLUMN IF NOT EXISTS category TEXT;
