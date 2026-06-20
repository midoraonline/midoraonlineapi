-- Phase 6: Enhanced listing scoring — engagement events, fraud/report penalties
-- Replaces the previous recalculate_product_listing_score with a richer formula.

CREATE OR REPLACE FUNCTION public.recalculate_product_listing_score(p_product_id uuid)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_views BIGINT;
  v_likes INT;
  v_whatsapp INT := 0;
  v_saves INT := 0;
  v_shares INT := 0;
  v_messages INT := 0;
  v_reports INT := 0;
  v_hours_since_created NUMERIC;
  v_recency_score NUMERIC;
  v_boost_bonus INTEGER := 0;
  v_shop_trust NUMERIC(3,2);
  v_shop_fraud NUMERIC(3,2);
  v_score NUMERIC;
BEGIN
  -- Core metrics from products and likes
  SELECT COALESCE(view_count, 0) INTO v_views
  FROM public.products WHERE id = p_product_id;

  SELECT COUNT(*) INTO v_likes
  FROM public.product_likes WHERE product_id = p_product_id;

  SELECT EXTRACT(EPOCH FROM (now() - created_at)) / 3600 INTO v_hours_since_created
  FROM public.products WHERE id = p_product_id;

  -- Engagement events from listing_events
  SELECT COALESCE(COUNT(*), 0) INTO v_whatsapp
  FROM public.listing_events
  WHERE listing_id = p_product_id AND event_type = 'whatsapp_clicked';

  SELECT COALESCE(COUNT(*), 0) INTO v_saves
  FROM public.listing_events
  WHERE listing_id = p_product_id AND event_type = 'saved';

  SELECT COALESCE(COUNT(*), 0) INTO v_shares
  FROM public.listing_events
  WHERE listing_id = p_product_id AND event_type = 'shared';

  SELECT COALESCE(COUNT(*), 0) INTO v_messages
  FROM public.listing_events
  WHERE listing_id = p_product_id AND event_type = 'messaged';

  SELECT COALESCE(COUNT(*), 0) INTO v_reports
  FROM public.listing_events
  WHERE listing_id = p_product_id AND event_type = 'reported';

  -- Recency: max 50 points, linear decay over 168 hours (7 days)
  v_recency_score := LEAST(GREATEST(50.0 - (v_hours_since_created * 50.0 / 168.0), 0), 50.0);

  -- Active boost bonus
  SELECT COALESCE(SUM(lb.score_bonus), 0) INTO v_boost_bonus
  FROM public.listing_boosts lb
  WHERE lb.listing_id = p_product_id
    AND lb.active = true
    AND lb.ends_at > now();

  -- Shop trust score and fraud score
  SELECT COALESCE(s.trust_score, 0), COALESCE(s.fraud_score, 0)
  INTO v_shop_trust, v_shop_fraud
  FROM public.shops s
  JOIN public.products p ON p.shop_id = s.id
  WHERE p.id = p_product_id;

  -- Score formula: positive signals minus penalties
  v_score := (
    v_recency_score
    + LEAST(LOG(2, GREATEST(v_views, 1)) * 3.0, 45.0)
    + LEAST(v_likes::NUMERIC, 50.0) * 2.0
    + LEAST(v_whatsapp::NUMERIC, 30.0) * 4.0
    + LEAST(v_saves::NUMERIC, 30.0) * 3.0
    + LEAST(v_shares::NUMERIC, 20.0) * 2.0
    + LEAST(v_messages::NUMERIC, 20.0) * 3.0
    + COALESCE(v_boost_bonus, 0)
    + COALESCE(v_shop_trust, 0) * 10.0
    - LEAST(v_reports::NUMERIC, 20.0) * 15.0
    - COALESCE(v_shop_fraud, 0) * 20.0
  );

  v_score := GREATEST(0, ROUND(v_score));

  UPDATE public.products SET listing_score = v_score::INTEGER WHERE id = p_product_id;
  RETURN v_score::INTEGER;
END;
$$;

GRANT EXECUTE ON FUNCTION public.recalculate_product_listing_score(uuid) TO service_role;
