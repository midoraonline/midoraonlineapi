-- Phase 38: admin-configurable category metadata (extra listing fields per
-- category, e.g. brand/model for Electronics) + Maids & Domestic Work
-- opportunity subcategory.

ALTER TABLE public.categories
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '[]'::jsonb;

INSERT INTO public.categories (slug, label, sort_order, parent_slug) VALUES
  ('maids-and-domestic-work', 'Maids & Domestic Work', 6208, 'opportunities')
ON CONFLICT (slug) DO NOTHING;
