-- Shop opening hours live on shops.availability (JSONB), which already exists
-- in production. This keeps the column present on databases that skipped the
-- base schema, and wraps any JSON string into the canonical object so the
-- old text stays readable. Structured objects are left untouched.
-- One statement. Safe to re-run.

DO $hours$
BEGIN
    IF to_regclass('public.shops') IS NULL THEN
        RAISE NOTICE 'shops is missing; skipped opening-hours migration';
        RETURN;
    END IF;

    EXECUTE 'ALTER TABLE public.shops ADD COLUMN IF NOT EXISTS availability jsonb';

    EXECUTE $sql$
        UPDATE public.shops
           SET availability = jsonb_build_object(
                'timezone', 'Africa/Kampala',
                'open_24_hours', false,
                'by_appointment', false,
                'note', NULL,
                'legacy_text', availability #>> '{}',
                'days', NULL
           )
         WHERE availability IS NOT NULL
           AND jsonb_typeof(availability) = 'string'
    $sql$;
END
$hours$;
