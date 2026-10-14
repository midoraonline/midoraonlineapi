-- Password reset for the custom email/password auth (public.users).
-- Only a SHA-256 hash of the emailed token is stored. Safe to re-run.
CREATE TABLE IF NOT EXISTS public.password_reset_tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS password_reset_tokens_user_idx
    ON public.password_reset_tokens (user_id, created_at DESC);
ALTER TABLE public.password_reset_tokens ENABLE ROW LEVEL SECURITY;
DO $g$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.password_reset_tokens TO service_role';
    END IF;
END
$g$;
