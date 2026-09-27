-- Reviews, listing close-statuses, and platform feature switches.
-- One statement. Safe to re-run in the Supabase SQL Editor.
-- Drops product_reviews triggers that still write products.average_rating
-- or products.rating_count (those columns are gone). Does not recreate them.

DO $fix$
DECLARE
    trg record;
    con record;
    src text;
BEGIN
    IF to_regclass('public.product_reviews') IS NOT NULL THEN
        FOR trg IN
            SELECT t.tgname AS tgname, p.oid AS fn_oid
            FROM pg_trigger t
            JOIN pg_proc p ON p.oid = t.tgfoid
            WHERE t.tgrelid = 'public.product_reviews'::regclass
              AND NOT t.tgisinternal
        LOOP
            BEGIN
                src := pg_get_functiondef(trg.fn_oid);
            EXCEPTION WHEN OTHERS THEN
                src := '';
            END;
            IF src ~* 'products\.average_rating'
               OR src ~* 'products\.rating_count'
               OR src ~* 'SET[[:space:]]+average_rating'
               OR src ~* 'SET[[:space:]]+rating_count'
            THEN
                EXECUTE format(
                    'DROP TRIGGER IF EXISTS %I ON public.product_reviews',
                    trg.tgname
                );
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_trigger t2
                    WHERE t2.tgfoid = trg.fn_oid
                      AND NOT t2.tgisinternal
                ) THEN
                    EXECUTE format('DROP FUNCTION IF EXISTS %s', trg.fn_oid::regprocedure);
                END IF;
            END IF;
        END LOOP;

        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'product_reviews'
              AND column_name = 'updated_at'
        ) THEN
            EXECUTE 'ALTER TABLE public.product_reviews ALTER COLUMN updated_at SET DEFAULT now()';
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conrelid = 'public.product_reviews'::regclass
              AND contype = 'u'
              AND pg_get_constraintdef(oid) ILIKE '%product_id%'
              AND pg_get_constraintdef(oid) ILIKE '%user_id%'
        ) THEN
            BEGIN
                EXECUTE
                    'ALTER TABLE public.product_reviews '
                    'ADD CONSTRAINT product_reviews_product_id_user_id_key '
                    'UNIQUE (product_id, user_id)';
            EXCEPTION
                WHEN duplicate_object OR unique_violation THEN
                    RAISE NOTICE 'product_reviews unique (product_id, user_id) skipped: %', SQLERRM;
            END;
        END IF;
    ELSE
        RAISE NOTICE 'product_reviews is missing; skipped review trigger cleanup';
    END IF;

    IF to_regclass('public.products') IS NOT NULL THEN
        FOR con IN
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid
             AND a.attnum = ANY (c.conkey)
            WHERE c.conrelid = 'public.products'::regclass
              AND c.contype = 'c'
              AND a.attname = 'status'
        LOOP
            EXECUTE format('ALTER TABLE public.products DROP CONSTRAINT %I', con.conname);
        END LOOP;
        EXECUTE 'ALTER TABLE public.products DROP CONSTRAINT IF EXISTS products_status_check';
        EXECUTE $sql$
            ALTER TABLE public.products
                ADD CONSTRAINT products_status_check
                CHECK (status IN (
                    'draft', 'pending_review', 'active', 'hidden', 'rejected', 'expired',
                    'sold', 'unavailable', 'filled', 'closed'
                ))
        $sql$;
    ELSE
        RAISE NOTICE 'products is missing; skipped status check';
    END IF;

    EXECUTE $sql$
        CREATE TABLE IF NOT EXISTS public.app_settings (
            key text PRIMARY KEY,
            enabled boolean NOT NULL DEFAULT false,
            value jsonb NOT NULL DEFAULT '{}'::jsonb,
            updated_by uuid,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    $sql$;
    EXECUTE $sql$
        CREATE TABLE IF NOT EXISTS public.app_settings_audit (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            key text NOT NULL,
            enabled boolean,
            value jsonb,
            previous_enabled boolean,
            previous_value jsonb,
            updated_by uuid,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    $sql$;
    EXECUTE $sql$
        INSERT INTO public.app_settings (key, enabled)
        VALUES
            ('analytics', false),
            ('signups_allowed', true),
            ('listings_require_review', false),
            ('ai_moderation', true),
            ('maintenance_mode', false)
        ON CONFLICT (key) DO NOTHING
    $sql$;
    EXECUTE 'ALTER TABLE public.app_settings ENABLE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE public.app_settings_audit ENABLE ROW LEVEL SECURITY';

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.app_settings TO service_role';
        EXECUTE 'GRANT SELECT, INSERT ON public.app_settings_audit TO service_role';
    END IF;
END
$fix$;
