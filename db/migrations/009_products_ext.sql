-- Phase 1d: Extend products table with listing workflow, ranking & extended types
ALTER TABLE public.products
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('draft', 'pending_review', 'active', 'hidden', 'rejected', 'expired', 'sold')),
  ADD COLUMN IF NOT EXISTS listing_score INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS location_name TEXT;

-- Extend item_type check to include property and job
ALTER TABLE public.products
  DROP CONSTRAINT IF EXISTS products_item_type_check;

ALTER TABLE public.products
  ADD CONSTRAINT products_item_type_check
    CHECK (item_type IN ('product', 'service', 'property', 'job'));

CREATE INDEX IF NOT EXISTS idx_products_status ON public.products(status);
CREATE INDEX IF NOT EXISTS idx_products_listing_score ON public.products(listing_score DESC);
CREATE INDEX IF NOT EXISTS idx_products_location_name ON public.products(location_name);

-- Atomic function to calculate listing_score used by the scoring engine
CREATE OR REPLACE FUNCTION public.recalculate_product_listing_score(p_product_id uuid)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_views BIGINT;
  v_likes INT;
  v_hours_since_created NUMERIC;
  v_recency_score NUMERIC;
  v_boost_bonus INTEGER := 0;
  v_shop_trust NUMERIC(3,2);
  v_score NUMERIC;
BEGIN
  SELECT COALESCE(view_count, 0) INTO v_views
  FROM public.products WHERE id = p_product_id;

  SELECT COUNT(*) INTO v_likes
  FROM public.product_likes WHERE product_id = p_product_id;

  SELECT EXTRACT(EPOCH FROM (now() - created_at)) / 3600 INTO v_hours_since_created
  FROM public.products WHERE id = p_product_id;

  -- Recency: max 50 points, decays over 168 hours (7 days)
  v_recency_score := LEAST(GREATEST(50.0 - (v_hours_since_created * 50.0 / 168.0), 0), 50.0);

  SELECT COALESCE(SUM(lb.score_bonus), 0) INTO v_boost_bonus
  FROM public.listing_boosts lb
  WHERE lb.listing_id = p_product_id
    AND lb.active = true
    AND lb.ends_at > now();

  SELECT COALESCE(trust_score, 0) INTO v_shop_trust
  FROM public.shops s
  JOIN public.products p ON p.shop_id = s.id
  WHERE p.id = p_product_id;

  v_score := (
    v_recency_score
    + LEAST(LOG(2, GREATEST(v_views, 1)), 15.0) * 3.0
    + LEAST(v_likes, 50) * 2.0
    + COALESCE(v_boost_bonus, 0)
    + COALESCE(v_shop_trust, 0) * 10.0
  );

  v_score := GREATEST(0, ROUND(v_score));

  UPDATE public.products SET listing_score = v_score::INTEGER WHERE id = p_product_id;
  RETURN v_score::INTEGER;
END;
$$;

GRANT EXECUTE ON FUNCTION public.recalculate_product_listing_score(uuid) TO service_role;
