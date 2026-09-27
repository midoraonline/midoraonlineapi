-- Account settings: optional profile bio and validated preference JSON.
-- preferences is written by the API after Pydantic validation. The default
-- matches auth.preferences.DEFAULT_PREFERENCES so existing users get theme
-- "system" and every notification toggle on.
-- One statement. Safe to re-run.

DO $prefs$
BEGIN
    IF to_regclass('public.profiles') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS bio text';
    ELSE
        RAISE NOTICE 'profiles is missing; skipped bio';
    END IF;

    IF to_regclass('public.users') IS NULL THEN
        RAISE NOTICE 'users is missing; skipped preferences';
        RETURN;
    END IF;

    EXECUTE $sql$
        ALTER TABLE public.users
            ADD COLUMN IF NOT EXISTS preferences jsonb NOT NULL
            DEFAULT '{"theme":"system","notifications":{"push":true,"email":true,"messages":true,"listing_approved":true,"listing_rejected":true,"reviews":true,"reports":true}}'::jsonb
    $sql$;
END
$prefs$;
