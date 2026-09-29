-- Contact verification before the first listing.
-- Extends the OTP ledger from 036 so email codes share verification_codes,
-- and grandfathers sellers who already have a listing.
-- One statement. Safe to re-run in the Supabase SQL Editor.

DO $verify$
DECLARE
    con record;
BEGIN
    IF to_regclass('public.users') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.users ADD COLUMN IF NOT EXISTS phone_verified boolean NOT NULL DEFAULT false';
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'google_sub'
        ) THEN
            EXECUTE $sql$
                UPDATE public.users
                SET email_verified = true
                WHERE google_sub IS NOT NULL
                  AND email_verified IS NOT TRUE
            $sql$;
        END IF;
    END IF;

    IF to_regclass('public.shops') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.shops ADD COLUMN IF NOT EXISTS whatsapp_verified boolean NOT NULL DEFAULT false';
    END IF;

    EXECUTE $sql$
        CREATE TABLE IF NOT EXISTS public.verification_codes (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id      uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            purpose      text NOT NULL CHECK (purpose IN ('phone', 'whatsapp', 'email')),
            target_type  text NOT NULL DEFAULT 'user' CHECK (target_type IN ('user', 'shop')),
            target_id    uuid NOT NULL,
            phone_number text NOT NULL,
            code_hash    text NOT NULL,
            channel      text NOT NULL CHECK (channel IN ('sms', 'whatsapp', 'email')),
            attempts     integer NOT NULL DEFAULT 0,
            max_attempts integer NOT NULL DEFAULT 5,
            expires_at   timestamptz NOT NULL,
            verified_at  timestamptz,
            created_at   timestamptz NOT NULL DEFAULT now()
        )
    $sql$;

    IF to_regclass('public.verification_codes') IS NOT NULL THEN
        FOR con IN
            SELECT c.conname
            FROM pg_constraint c
            WHERE c.conrelid = 'public.verification_codes'::regclass
              AND c.contype = 'c'
              AND (
                    pg_get_constraintdef(c.oid) ILIKE '%purpose%'
                    OR pg_get_constraintdef(c.oid) ILIKE '%channel%'
                  )
        LOOP
            EXECUTE format(
                'ALTER TABLE public.verification_codes DROP CONSTRAINT %I',
                con.conname
            );
        END LOOP;
        EXECUTE 'ALTER TABLE public.verification_codes DROP CONSTRAINT IF EXISTS verification_codes_purpose_check';
        EXECUTE 'ALTER TABLE public.verification_codes DROP CONSTRAINT IF EXISTS verification_codes_channel_check';
        EXECUTE $sql$
            ALTER TABLE public.verification_codes
                ADD CONSTRAINT verification_codes_purpose_check
                CHECK (purpose IN ('phone', 'whatsapp', 'email'))
        $sql$;
        EXECUTE $sql$
            ALTER TABLE public.verification_codes
                ADD CONSTRAINT verification_codes_channel_check
                CHECK (channel IN ('sms', 'whatsapp', 'email'))
        $sql$;
        EXECUTE 'ALTER TABLE public.verification_codes ENABLE ROW LEVEL SECURITY';
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
            EXECUTE 'DROP POLICY IF EXISTS "service role only" ON public.verification_codes';
            EXECUTE $sql$
                CREATE POLICY "service role only"
                    ON public.verification_codes
                    FOR ALL
                    TO service_role
                    USING (true)
                    WITH CHECK (true)
            $sql$;
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.verification_codes TO service_role';
        END IF;
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_verification_codes_target ON public.verification_codes (target_type, target_id, purpose, created_at DESC)';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_verification_codes_user ON public.verification_codes (user_id, created_at DESC)';
    END IF;

    IF to_regclass('public.users') IS NOT NULL
       AND to_regclass('public.shops') IS NOT NULL
       AND to_regclass('public.products') IS NOT NULL THEN
        EXECUTE $sql$
            UPDATE public.users u
            SET email_verified = true,
                phone_verified = CASE
                    WHEN NULLIF(btrim(u.phone_number), '') IS NOT NULL THEN true
                    ELSE u.phone_verified
                END
            WHERE EXISTS (
                SELECT 1
                FROM public.shops s
                JOIN public.products p ON p.shop_id = s.id
                WHERE s.owner_id = u.id
            )
        $sql$;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role')
       AND to_regclass('public.verification_codes') IS NOT NULL THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.verification_codes TO service_role';
    END IF;
END
$verify$;
