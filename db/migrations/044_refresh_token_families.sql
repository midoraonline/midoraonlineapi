-- Scope refresh-token reuse to a login family.
-- Each login inserts a new family_id; rotation copies it.
-- Existing rows become their own family so a stale token cannot
-- revoke a newer login.
-- Run before deploying the API that writes family_id.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE public.refresh_tokens
    ADD COLUMN IF NOT EXISTS family_id UUID;

UPDATE public.refresh_tokens
SET family_id = jti
WHERE family_id IS NULL;

ALTER TABLE public.refresh_tokens
    ALTER COLUMN family_id SET DEFAULT gen_random_uuid();

ALTER TABLE public.refresh_tokens
    ALTER COLUMN family_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_family_id
    ON public.refresh_tokens (family_id);
