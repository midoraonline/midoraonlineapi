-- 004_reposts.sql
-- Create table to track product reposts to limit frequency (e.g. 2x per day)

CREATE TABLE IF NOT EXISTS public.product_reposts_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.product_reposts_log ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_product_reposts_log_product_id ON public.product_reposts_log(product_id);
