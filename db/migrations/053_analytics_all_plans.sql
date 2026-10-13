-- Platform switch: give every subscription plan access to shop analytics.
-- Read by payments.plan_service.has_analytics_access. Safe to re-run.
DO $m$
BEGIN
    IF to_regclass('public.app_settings') IS NULL THEN
        RAISE NOTICE 'app_settings is missing; apply 050 first';
        RETURN;
    END IF;
    INSERT INTO public.app_settings (key, enabled)
    VALUES ('analytics_all_plans', false)
    ON CONFLICT (key) DO NOTHING;
END
$m$;
