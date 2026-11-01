ALTER TABLE public.feed_rank_snapshots ADD COLUMN IF NOT EXISTS version text;
ALTER TABLE public.feed_rank_snapshots ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
DELETE FROM public.feed_rank_snapshots WHERE snapshot_key NOT IN ('home', 'home:prev');
GRANT ALL ON public.feed_rank_snapshots TO service_role;

DROP FUNCTION IF EXISTS public.match_feed_products(vector, integer, uuid[]);
DROP TABLE IF EXISTS public.user_feed_cache;

CREATE OR REPLACE FUNCTION public.recalculate_product_listing_score(p_product_id uuid)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_views INT := 0;
  v_views_from_events INT := 0;
  v_views_from_product BIGINT := 0;
  v_likes INT;
  v_whatsapp INT := 0;
  v_saves INT := 0;
  v_shares INT := 0;
  v_messages INT := 0;
  v_reports INT := 0;
  v_hours_since_created NUMERIC;
  v_recency_score NUMERIC;
  v_shop_trust NUMERIC(3,2);
  v_shop_fraud NUMERIC(3,2);
  v_score NUMERIC;
BEGIN
  SELECT COALESCE(view_count, 0) INTO v_views_from_product
  FROM public.products WHERE id = p_product_id;

  SELECT COALESCE(COUNT(*), 0) INTO v_views_from_events
  FROM public.listing_events
  WHERE listing_id = p_product_id AND event_type = 'viewed';

  v_views := GREATEST(v_views_from_product, v_views_from_events)::INT;

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

  SELECT COUNT(*) INTO v_likes
  FROM public.product_likes WHERE product_id = p_product_id;

  SELECT EXTRACT(EPOCH FROM (now() - created_at)) / 3600 INTO v_hours_since_created
  FROM public.products WHERE id = p_product_id;
  v_recency_score := LEAST(GREATEST(50.0 - (v_hours_since_created * 50.0 / 168.0), 0), 50.0);

  SELECT COALESCE(s.trust_score, 0), COALESCE(s.fraud_score, 0)
  INTO v_shop_trust, v_shop_fraud
  FROM public.shops s
  JOIN public.products p ON p.shop_id = s.id
  WHERE p.id = p_product_id;

  v_score := (
    v_recency_score
    + LEAST(LOG(2, GREATEST(v_views, 1)) * 3.0, 45.0)
    + LEAST(v_likes::NUMERIC, 50.0) * 2.0
    + LEAST(v_whatsapp::NUMERIC, 30.0) * 4.0
    + LEAST(v_saves::NUMERIC, 30.0) * 3.0
    + LEAST(v_shares::NUMERIC, 20.0) * 2.0
    + LEAST(v_messages::NUMERIC, 20.0) * 3.0
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

DO $rescore$
DECLARE r record;
BEGIN
  FOR r IN SELECT DISTINCT listing_id FROM public.listing_boosts LOOP
    PERFORM public.recalculate_product_listing_score(r.listing_id);
  END LOOP;
END
$rescore$;
