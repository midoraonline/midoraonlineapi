-- Phase 14: Product review workflow & message event tracking

-- 1. Add 'messaged' to listing_events CHECK constraint
-- PostgreSQL requires dropping and recreating the constraint
ALTER TABLE public.listing_events
  DROP CONSTRAINT IF EXISTS listing_events_event_type_check;

ALTER TABLE public.listing_events
  ADD CONSTRAINT listing_events_event_type_check
  CHECK (event_type IN (
    'viewed', 'whatsapp_clicked', 'call_clicked', 'saved',
    'shared', 'reported', 'updated', 'messaged'
  ));

-- 2. Add review tracking fields to products
ALTER TABLE public.products
  ADD COLUMN IF NOT EXISTS reviewed_by UUID REFERENCES public.users(id),
  ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS review_notes TEXT;

-- 3. Add index for pending_review filtering
CREATE INDEX IF NOT EXISTS idx_products_status_reviewed
  ON public.products(status, reviewed_at DESC NULLS FIRST);

-- 4. Add index for seller lookup on listing_events
CREATE INDEX IF NOT EXISTS idx_listing_events_buyer_id
  ON public.listing_events(buyer_id);

-- 5. Add index for product stats by event type
CREATE INDEX IF NOT EXISTS idx_listing_events_product_event
  ON public.listing_events(listing_id, event_type);
