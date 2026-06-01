-- Phase 11: Enhanced verification documents & comments

-- Add verification document fields to shop_verifications
ALTER TABLE public.shop_verifications
  ADD COLUMN IF NOT EXISTS submitted_docs JSONB DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS submitted_phone TEXT,
  ADD COLUMN IF NOT EXISTS submitted_whatsapp TEXT,
  ADD COLUMN IF NOT EXISTS submitted_location TEXT,
  ADD COLUMN IF NOT EXISTS shop_duration_days INTEGER DEFAULT 0;

-- Comments on products
CREATE TABLE IF NOT EXISTS public.product_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.users(id),
    comment TEXT NOT NULL,
    is_flagged BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.product_comments ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_product_comments_product_id ON public.product_comments(product_id);
CREATE INDEX IF NOT EXISTS idx_product_comments_created_at ON public.product_comments(created_at DESC);

-- Comments on shops
CREATE TABLE IF NOT EXISTS public.shop_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.users(id),
    comment TEXT NOT NULL,
    is_flagged BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.shop_comments ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_shop_comments_shop_id ON public.shop_comments(shop_id);
CREATE INDEX IF NOT EXISTS idx_shop_comments_created_at ON public.shop_comments(created_at DESC);

-- Reports on listings
CREATE TABLE IF NOT EXISTS public.product_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    reporter_id UUID NOT NULL REFERENCES public.users(id),
    reason TEXT NOT NULL,
    description TEXT,
    resolved BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.product_reports ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_product_reports_product_id ON public.product_reports(product_id);
CREATE INDEX IF NOT EXISTS idx_product_reports_resolved ON public.product_reports(resolved);

-- Shop duration helper function
CREATE OR REPLACE FUNCTION public.calculate_shop_duration(p_shop_id uuid)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_days INTEGER;
BEGIN
  SELECT EXTRACT(DAY FROM (now() - created_at))::INTEGER INTO v_days
  FROM public.shops WHERE id = p_shop_id;
  RETURN COALESCE(v_days, 0);
END;
$$;

GRANT EXECUTE ON FUNCTION public.calculate_shop_duration(uuid) TO service_role;

-- Update verification to auto-calculate shop duration on submit
CREATE OR REPLACE FUNCTION public.submit_verification_with_docs(
  p_shop_id uuid,
  p_notes text,
  p_submitted_docs jsonb,
  p_submitted_phone text,
  p_submitted_whatsapp text,
  p_submitted_location text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_shop_days INTEGER;
  v_result jsonb;
BEGIN
  SELECT EXTRACT(DAY FROM (now() - created_at))::INTEGER INTO v_shop_days
  FROM public.shops WHERE id = p_shop_id;

  INSERT INTO public.shop_verifications (shop_id, status, notes, submitted_docs, submitted_phone, submitted_whatsapp, submitted_location, shop_duration_days)
  VALUES (p_shop_id, 'pending', p_notes, p_submitted_docs, p_submitted_phone, p_submitted_whatsapp, p_submitted_location, COALESCE(v_shop_days, 0))
  ON CONFLICT (shop_id) DO UPDATE SET
    status = 'pending',
    notes = p_notes,
    submitted_docs = p_submitted_docs,
    submitted_phone = p_submitted_phone,
    submitted_whatsapp = p_submitted_whatsapp,
    submitted_location = p_submitted_location,
    shop_duration_days = COALESCE(v_shop_days, 0),
    requested_at = now(),
    reviewed_at = NULL,
    reviewed_by = NULL
  RETURNING row_to_json(shop_verifications) INTO v_result;

  RETURN v_result;
END;
$$;

GRANT EXECUTE ON FUNCTION public.submit_verification_with_docs(uuid, text, jsonb, text, text, text) TO service_role;
