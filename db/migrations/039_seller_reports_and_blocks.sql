-- Phase 1 Must: buyer report-seller + block-seller (alongside existing product_reports).

CREATE TABLE IF NOT EXISTS public.seller_reports (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    reporter_id   UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    shop_id       UUID REFERENCES public.shops(id) ON DELETE SET NULL,
    reason        TEXT NOT NULL,
    description   TEXT,
    resolved      BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_seller_reports_seller
    ON public.seller_reports(seller_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_seller_reports_open
    ON public.seller_reports(resolved, created_at DESC);

ALTER TABLE public.seller_reports ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service role only" ON public.seller_reports;
CREATE POLICY "service role only"
    ON public.seller_reports
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE TABLE IF NOT EXISTS public.seller_blocks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blocker_id    UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    seller_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (blocker_id, seller_id),
    CHECK (blocker_id <> seller_id)
);

CREATE INDEX IF NOT EXISTS idx_seller_blocks_blocker
    ON public.seller_blocks(blocker_id);
CREATE INDEX IF NOT EXISTS idx_seller_blocks_seller
    ON public.seller_blocks(seller_id);

ALTER TABLE public.seller_blocks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service role only" ON public.seller_blocks;
CREATE POLICY "service role only"
    ON public.seller_blocks
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
