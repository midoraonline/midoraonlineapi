-- Shop and product view (click) counters. Run after shop engagement migration if split.

ALTER TABLE public.shops ADD COLUMN IF NOT EXISTS view_count BIGINT NOT NULL DEFAULT 0;
ALTER TABLE public.products ADD COLUMN IF NOT EXISTS view_count BIGINT NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION public.increment_shop_view_count(p_shop_id uuid)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE v bigint;
BEGIN
  UPDATE public.shops
  SET view_count = view_count + 1, updated_at = now()
  WHERE id = p_shop_id
  RETURNING view_count INTO v;
  RETURN v;
END;
$$;

CREATE OR REPLACE FUNCTION public.increment_product_view_count(p_product_id uuid)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE v bigint;
BEGIN
  UPDATE public.products
  SET view_count = view_count + 1
  WHERE id = p_product_id
  RETURNING view_count INTO v;
  RETURN v;
END;
$$;

GRANT EXECUTE ON FUNCTION public.increment_shop_view_count(uuid) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.increment_product_view_count(uuid) TO anon, authenticated, service_role;
