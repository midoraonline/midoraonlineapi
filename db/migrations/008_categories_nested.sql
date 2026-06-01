-- Phase 1c: Add parent_slug for category nesting
ALTER TABLE public.categories
  ADD COLUMN IF NOT EXISTS parent_slug TEXT REFERENCES public.categories(slug);

CREATE INDEX IF NOT EXISTS idx_categories_parent_slug ON public.categories(parent_slug);
