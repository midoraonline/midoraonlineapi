-- Category field definitions (PR #4) read and write public.categories.metadata.
-- Migration 038 adds that column (and the Maids subcategory) but it was not
-- applied in production, so admin field edits and any query of c.metadata fail.
-- parent_slug comes from 008. Both statements are idempotent.
-- Run this in the Supabase SQL Editor before the services/opportunities seed.
-- Default is a JSON array, which is the shape the API stores.

ALTER TABLE public.categories
    ADD COLUMN IF NOT EXISTS parent_slug TEXT REFERENCES public.categories(slug);

CREATE INDEX IF NOT EXISTS idx_categories_parent_slug
    ON public.categories(parent_slug);

ALTER TABLE public.categories
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '[]'::jsonb;

INSERT INTO public.categories (slug, label, sort_order, parent_slug)
SELECT 'maids-and-domestic-work', 'Maids & Domestic Work', 6208, 'opportunities'
WHERE EXISTS (
    SELECT 1 FROM public.categories WHERE slug = 'opportunities'
)
ON CONFLICT (slug) DO NOTHING;
