-- Phase 66: only shops whose WhatsApp number is really on WhatsApp expose it.
--
-- whatsapp_registered  NULL = never checked / check unavailable, true/false = library lookup result.
-- whatsapp_verified    already set by the OTP flow (036); proves the owner controls the number.
-- whatsapp_public      the ONLY column public queries read (aliased as whatsapp_number). It is the
--                      number when the shop is OTP-verified or confirmed on WhatsApp, else NULL.
--                      Generated, so no code path can forget the rule.
--
-- Deploy order: run this migration BEFORE deploying the API/frontend that select whatsapp_public.
-- Re-runnable in the Supabase SQL Editor.

DO $wa$
BEGIN
    IF to_regclass('public.shops') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.shops ADD COLUMN IF NOT EXISTS whatsapp_verified boolean NOT NULL DEFAULT false';
        EXECUTE 'ALTER TABLE public.shops ADD COLUMN IF NOT EXISTS whatsapp_registered boolean';
        EXECUTE 'ALTER TABLE public.shops ADD COLUMN IF NOT EXISTS whatsapp_checked_at timestamptz';
        EXECUTE 'ALTER TABLE public.shops ADD COLUMN IF NOT EXISTS whatsapp_check_source text';
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'shops' AND column_name = 'whatsapp_public'
        ) THEN
            EXECUTE $sql$
                ALTER TABLE public.shops ADD COLUMN whatsapp_public text
                GENERATED ALWAYS AS (
                    CASE WHEN whatsapp_verified OR whatsapp_registered IS TRUE THEN whatsapp_number END
                ) STORED
            $sql$;
        END IF;
    END IF;
END
$wa$;

-- Shared across serverless instances so one number is looked up at most once per TTL, whichever
-- shop or request asks. Fewer live lookups = lower account-restriction risk on the checker number.
CREATE TABLE IF NOT EXISTS public.whatsapp_lookup_cache (
    e164        text PRIMARY KEY,
    registered  boolean NOT NULL,
    source      text NOT NULL DEFAULT 'library',
    checked_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.whatsapp_lookup_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service role only" ON public.whatsapp_lookup_cache;
CREATE POLICY "service role only" ON public.whatsapp_lookup_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
