-- Account deletion and admin user management.
-- * users.status gains 'deleted' (anonymized accounts kept for FK integrity).
-- * users.deleted_at / suspended_at record when it happened.
-- Safe to re-run.

DO $users$
BEGIN
    IF to_regclass('public.users') IS NULL THEN
        RAISE NOTICE 'users is missing; skipped';
        RETURN;
    END IF;

    ALTER TABLE public.users ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
    ALTER TABLE public.users ADD COLUMN IF NOT EXISTS suspended_at timestamptz;
    ALTER TABLE public.users ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active';

    ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_status_check;
    ALTER TABLE public.users
        ADD CONSTRAINT users_status_check
        CHECK (status IN ('active', 'suspended', 'blocked', 'deleted'));

    CREATE INDEX IF NOT EXISTS idx_users_status ON public.users (status);
END
$users$;
