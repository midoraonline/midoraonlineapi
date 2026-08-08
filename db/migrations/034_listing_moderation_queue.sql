-- Phase 34: Listing moderation queue.
--
-- Mirrors the mail_queue pattern: writes are cheap (POST returns 202), a cron
-- job drains pending rows a batch at a time. The claim RPC uses FOR UPDATE
-- SKIP LOCKED so a horizontally-scaled drain (multiple concurrent cron
-- invocations, or manual replays) cannot double-moderate the same row.
--
-- We snapshot title/description/image_urls at submission so re-moderation is
-- reproducible even if the underlying product row is later edited or deleted.

CREATE TABLE IF NOT EXISTS public.listing_moderation_queue (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id     UUID REFERENCES public.products(id) ON DELETE SET NULL,
    seller_id      UUID REFERENCES public.users(id) ON DELETE SET NULL,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    image_urls     TEXT[] NOT NULL DEFAULT '{}'::text[],
    status         TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'approved', 'rejected', 'needs_review', 'failed')),
    reason         TEXT,
    scores         JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempts       INT NOT NULL DEFAULT 0,
    error          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processing_started_at TIMESTAMPTZ,
    decided_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_moderation_queue_pending
    ON public.listing_moderation_queue(status, created_at)
    WHERE status IN ('pending', 'processing');

CREATE INDEX IF NOT EXISTS idx_moderation_queue_product
    ON public.listing_moderation_queue(product_id);

CREATE INDEX IF NOT EXISTS idx_moderation_queue_seller
    ON public.listing_moderation_queue(seller_id, created_at DESC);


-- Perceptual-hash blocklist for known-bad images. Populated by admins /
-- reviewers when they reject content; the pipeline hits this before touching
-- Gemini, so obvious re-uploads are killed for free.
CREATE TABLE IF NOT EXISTS public.moderation_bad_image_hashes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phash       BIGINT NOT NULL,           -- 64-bit average hash, stored signed
    label       TEXT,                       -- optional: 'nudity' | 'weapon' | 'scam' ...
    added_by    UUID REFERENCES public.users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bad_image_hashes_phash
    ON public.moderation_bad_image_hashes(phash);


-- Claim up to N pending rows atomically. Called by the cron drain endpoint;
-- FOR UPDATE SKIP LOCKED means concurrent drains never see the same row.
CREATE OR REPLACE FUNCTION public.claim_moderation_queue_batch(p_limit INT DEFAULT 5)
RETURNS SETOF public.listing_moderation_queue
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  UPDATE public.listing_moderation_queue
  SET status = 'processing',
      processing_started_at = now(),
      attempts = attempts + 1
  WHERE id IN (
    SELECT id FROM public.listing_moderation_queue
    WHERE status = 'pending'
    ORDER BY created_at
    LIMIT p_limit
    FOR UPDATE SKIP LOCKED
  )
  RETURNING *;
END;
$$;


-- Reclaim rows that got stuck in 'processing' (e.g. Vercel function timed out
-- mid-run). Called at the top of each drain pass.
CREATE OR REPLACE FUNCTION public.reclaim_stuck_moderation_rows(p_older_than_seconds INT DEFAULT 300)
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
  v_count INT;
BEGIN
  UPDATE public.listing_moderation_queue
  SET status = 'pending'
  WHERE status = 'processing'
    AND processing_started_at < now() - make_interval(secs => p_older_than_seconds);
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;
