-- Login lookup + shared guest feed ranking.
-- users.email is already UNIQUE, so email login uses that index.
-- refresh_tokens.jti is the primary key. family_id is indexed in 044.
-- google_sub is new (production has no Google subject column).
-- One statement. Safe to re-run. Does not rewrite existing users.

DO $speed$
BEGIN
    IF to_regclass('public.users') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.users ADD COLUMN IF NOT EXISTS google_sub text';
        EXECUTE '
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub
                ON public.users (google_sub)
                WHERE google_sub IS NOT NULL
        ';
    END IF;

    EXECUTE '
        CREATE TABLE IF NOT EXISTS public.feed_rank_snapshots (
            snapshot_key text PRIMARY KEY,
            ranked_ids jsonb NOT NULL DEFAULT ''[]''::jsonb,
            refreshed_at timestamptz NOT NULL DEFAULT now()
        )
    ';
    EXECUTE 'ALTER TABLE public.feed_rank_snapshots ENABLE ROW LEVEL SECURITY';

    IF to_regclass('public.product_reviews') IS NULL THEN
        RAISE NOTICE 'product_reviews is missing; skipped product_review_stats';
        RETURN;
    END IF;

    EXECUTE $fn$
        CREATE OR REPLACE FUNCTION public.product_review_stats(p_ids uuid[])
        RETURNS TABLE(product_id uuid, average_rating numeric, review_count integer)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $body$
            SELECT r.product_id,
                   round(avg(r.rating)::numeric, 2) AS average_rating,
                   count(*)::integer AS review_count
              FROM public.product_reviews r
             WHERE r.product_id = ANY(p_ids)
             GROUP BY r.product_id
        $body$
    $fn$;

    EXECUTE 'GRANT EXECUTE ON FUNCTION public.product_review_stats(uuid[]) TO service_role';
END
$speed$;
