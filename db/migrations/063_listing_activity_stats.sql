CREATE INDEX IF NOT EXISTS idx_listing_events_listing_time
    ON public.listing_events (listing_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_product_likes_product_time
    ON public.product_likes (product_id, created_at DESC);

CREATE OR REPLACE FUNCTION public.listing_activity_stats(p_listing_ids uuid[], p_since timestamptz)
RETURNS TABLE (
    listing_id uuid,
    impressions bigint,
    reach bigint,
    boosted_impressions bigint,
    views bigint,
    unique_viewers bigint,
    whatsapp_clicks bigint,
    call_clicks bigint,
    messages bigint,
    shares bigint,
    likes bigint,
    comments bigint,
    reviews bigint,
    reports bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    WITH ids AS (
        SELECT p.id, s.owner_id
        FROM products p
        LEFT JOIN shops s ON s.id = p.shop_id
        WHERE p.id = ANY (p_listing_ids)
    ),
    imp AS (
        SELECT li.listing_id,
               count(*) AS impressions,
               count(DISTINCT coalesce(li.buyer_id::text, li.session_id, li.device_hash, li.id::text)) AS reach,
               count(*) FILTER (WHERE li.pool = 'boosted') AS boosted
        FROM listing_impressions li
        JOIN ids ON ids.id = li.listing_id
        WHERE li.created_at >= p_since
          AND li.buyer_id IS DISTINCT FROM ids.owner_id
        GROUP BY li.listing_id
    ),
    vw AS (
        SELECT ids.id AS listing_id,
               count(*) AS views,
               count(DISTINCT coalesce(ae.actor_id::text, ae.session_id, ae.id::text)) AS uniq
        FROM analytics_events ae
        JOIN ids ON ids.id::text = ae.target_id
        WHERE ae.event_type = 'listing:viewed'
          AND ae.target_type = 'product'
          AND ae.ts >= p_since
          AND ae.actor_id IS DISTINCT FROM ids.owner_id
        GROUP BY ids.id
    ),
    ev AS (
        SELECT le.listing_id,
               count(*) FILTER (WHERE le.event_type = 'whatsapp_clicked') AS wa,
               count(*) FILTER (WHERE le.event_type = 'call_clicked') AS calls,
               count(*) FILTER (WHERE le.event_type = 'messaged') AS msgs,
               count(*) FILTER (WHERE le.event_type = 'shared') AS shares
        FROM listing_events le
        JOIN ids ON ids.id = le.listing_id
        WHERE le.created_at >= p_since
          AND le.buyer_id IS DISTINCT FROM ids.owner_id
        GROUP BY le.listing_id
    ),
    lk AS (
        SELECT pl.product_id, count(*) AS n
        FROM product_likes pl
        JOIN ids ON ids.id = pl.product_id
        WHERE pl.created_at >= p_since AND pl.user_id IS DISTINCT FROM ids.owner_id
        GROUP BY pl.product_id
    ),
    cm AS (
        SELECT pc.product_id, count(*) AS n
        FROM product_comments pc
        JOIN ids ON ids.id = pc.product_id
        WHERE pc.created_at >= p_since AND pc.user_id IS DISTINCT FROM ids.owner_id
        GROUP BY pc.product_id
    ),
    rv AS (
        SELECT pr.product_id, count(*) AS n
        FROM product_reviews pr
        JOIN ids ON ids.id = pr.product_id
        WHERE pr.created_at >= p_since
        GROUP BY pr.product_id
    ),
    rp AS (
        SELECT r.product_id, count(*) AS n
        FROM product_reports r
        JOIN ids ON ids.id = r.product_id
        WHERE r.created_at >= p_since
        GROUP BY r.product_id
    )
    SELECT ids.id,
           coalesce(imp.impressions, 0),
           coalesce(imp.reach, 0),
           coalesce(imp.boosted, 0),
           coalesce(vw.views, 0),
           coalesce(vw.uniq, 0),
           coalesce(ev.wa, 0),
           coalesce(ev.calls, 0),
           coalesce(ev.msgs, 0),
           coalesce(ev.shares, 0),
           coalesce(lk.n, 0),
           coalesce(cm.n, 0),
           coalesce(rv.n, 0),
           coalesce(rp.n, 0)
    FROM ids
    LEFT JOIN imp ON imp.listing_id = ids.id
    LEFT JOIN vw ON vw.listing_id = ids.id
    LEFT JOIN ev ON ev.listing_id = ids.id
    LEFT JOIN lk ON lk.product_id = ids.id
    LEFT JOIN cm ON cm.product_id = ids.id
    LEFT JOIN rv ON rv.product_id = ids.id
    LEFT JOIN rp ON rp.product_id = ids.id;
$$;

CREATE OR REPLACE FUNCTION public.listing_activity_daily(p_listing_ids uuid[], p_since timestamptz)
RETURNS TABLE (day date, impressions bigint, views bigint, contacts bigint)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    WITH ids AS (
        SELECT p.id, s.owner_id
        FROM products p
        LEFT JOIN shops s ON s.id = p.shop_id
        WHERE p.id = ANY (p_listing_ids)
    ),
    hits AS (
        SELECT (li.created_at AT TIME ZONE 'Africa/Kampala')::date AS day, 1 AS i, 0 AS v, 0 AS c
        FROM listing_impressions li
        JOIN ids ON ids.id = li.listing_id
        WHERE li.created_at >= p_since AND li.buyer_id IS DISTINCT FROM ids.owner_id
        UNION ALL
        SELECT (ae.ts AT TIME ZONE 'Africa/Kampala')::date, 0, 1, 0
        FROM analytics_events ae
        JOIN ids ON ids.id::text = ae.target_id
        WHERE ae.event_type = 'listing:viewed'
          AND ae.target_type = 'product'
          AND ae.ts >= p_since
          AND ae.actor_id IS DISTINCT FROM ids.owner_id
        UNION ALL
        SELECT (le.created_at AT TIME ZONE 'Africa/Kampala')::date, 0, 0, 1
        FROM listing_events le
        JOIN ids ON ids.id = le.listing_id
        WHERE le.created_at >= p_since
          AND le.event_type IN ('whatsapp_clicked', 'call_clicked', 'messaged')
          AND le.buyer_id IS DISTINCT FROM ids.owner_id
    )
    SELECT day, sum(i)::bigint, sum(v)::bigint, sum(c)::bigint
    FROM hits
    GROUP BY day
    ORDER BY day;
$$;

REVOKE ALL ON FUNCTION public.listing_activity_stats(uuid[], timestamptz) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.listing_activity_daily(uuid[], timestamptz) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.listing_activity_stats(uuid[], timestamptz) TO service_role;
GRANT EXECUTE ON FUNCTION public.listing_activity_daily(uuid[], timestamptz) TO service_role;
