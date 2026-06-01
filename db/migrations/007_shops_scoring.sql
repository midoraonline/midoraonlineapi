-- Phase 1b: Extend shops table with seller scoring fields
ALTER TABLE public.shops
  ADD COLUMN IF NOT EXISTS trust_score NUMERIC(3,2) NOT NULL DEFAULT 0.00,
  ADD COLUMN IF NOT EXISTS fraud_score NUMERIC(3,2) NOT NULL DEFAULT 0.00,
  ADD COLUMN IF NOT EXISTS seller_score NUMERIC(6,2) NOT NULL DEFAULT 0.00,
  ADD COLUMN IF NOT EXISTS available_now BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_shops_seller_score ON public.shops(seller_score DESC);
CREATE INDEX IF NOT EXISTS idx_shops_trust_score ON public.shops(trust_score DESC);
CREATE INDEX IF NOT EXISTS idx_shops_available_now ON public.shops(available_now);

-- Atomic function to update seller_score used by the scoring engine
CREATE OR REPLACE FUNCTION public.recalculate_shop_seller_score(p_shop_id uuid)
RETURNS NUMERIC(6,2)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_trust NUMERIC(3,2);
  v_fraud NUMERIC(3,2);
  v_views BIGINT;
  v_products INT;
  v_score NUMERIC(6,2);
BEGIN
  SELECT trust_score, fraud_score INTO v_trust, v_fraud
  FROM public.shops WHERE id = p_shop_id;

  SELECT COALESCE(view_count, 0) INTO v_views
  FROM public.shops WHERE id = p_shop_id;

  SELECT COUNT(*) INTO v_products
  FROM public.products WHERE shop_id = p_shop_id AND is_published = true;

  v_score := (
    COALESCE(v_trust, 0) * 30.0
    + LEAST(LOG(2, GREATEST(v_views, 1)), 10.0) * 5.0
    + LEAST(v_products, 100) * 2.0
    - COALESCE(v_fraud, 0) * 50.0
  );

  v_score := GREATEST(0, v_score);

  UPDATE public.shops SET seller_score = v_score WHERE id = p_shop_id;
  RETURN v_score;
END;
$$;

GRANT EXECUTE ON FUNCTION public.recalculate_shop_seller_score(uuid) TO service_role;
