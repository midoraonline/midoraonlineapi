-- Phase 1 Should: store aHash per listing image for near-duplicate detection
-- across active listings (paired with same-seller title/price re-post check).

CREATE TABLE IF NOT EXISTS public.listing_image_hashes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id  UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    phash       BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_listing_image_hashes_phash
    ON public.listing_image_hashes(phash);

CREATE INDEX IF NOT EXISTS idx_listing_image_hashes_product
    ON public.listing_image_hashes(product_id);

ALTER TABLE public.listing_image_hashes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service role only" ON public.listing_image_hashes;
CREATE POLICY "service role only"
    ON public.listing_image_hashes
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
