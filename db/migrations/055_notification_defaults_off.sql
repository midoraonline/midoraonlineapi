-- New accounts start with every notification toggle OFF (opt-in).
-- Only the column DEFAULT changes; existing rows keep the values they
-- already have, so no saved choice is touched.
-- One statement. Safe to re-run.

DO $prefs$
BEGIN
    IF to_regclass('public.users') IS NULL THEN
        RAISE NOTICE 'users is missing; skipped';
        RETURN;
    END IF;
    EXECUTE $sql$
        ALTER TABLE public.users
            ALTER COLUMN preferences SET DEFAULT
            '{"theme":"system","notifications":{"push":false,"email":false,"messages":false,"listing_approved":false,"listing_rejected":false,"reviews":false,"reports":false}}'::jsonb
    $sql$;
END
$prefs$;
