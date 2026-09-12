-- Phase 36: Phone & WhatsApp OTP verification (Africa's Talking SMS/WhatsApp).
--
-- Adds a verified flag next to the two contact numbers we already collect
-- (users.phone_number, shops.whatsapp_number) plus a generic OTP ledger that
-- both flows share. `target_type`/`target_id` keep it reusable for either a
-- user's own phone or a shop's WhatsApp contact without duplicating tables.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS phone_verified BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE public.shops
    ADD COLUMN IF NOT EXISTS whatsapp_verified BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS public.verification_codes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    purpose      TEXT NOT NULL CHECK (purpose IN ('phone', 'whatsapp')),
    target_type  TEXT NOT NULL DEFAULT 'user' CHECK (target_type IN ('user', 'shop')),
    target_id    UUID NOT NULL,
    phone_number TEXT NOT NULL,
    code_hash    TEXT NOT NULL,
    channel      TEXT NOT NULL CHECK (channel IN ('sms', 'whatsapp')),
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    expires_at   TIMESTAMPTZ NOT NULL,
    verified_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.verification_codes ENABLE ROW LEVEL SECURITY;

-- Service role only — codes are never read/written from the browser directly.
DROP POLICY IF EXISTS "service role only" ON public.verification_codes;
CREATE POLICY "service role only"
    ON public.verification_codes
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE INDEX IF NOT EXISTS idx_verification_codes_target
    ON public.verification_codes(target_type, target_id, purpose, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_verification_codes_user
    ON public.verification_codes(user_id, created_at DESC);
