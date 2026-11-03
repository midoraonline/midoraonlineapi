-- Reset all tracked impressions, clicks, views, and derived personalization
-- data. NOT a numbered migration — run manually in the Supabase SQL editor
-- (or psql) when you want a clean slate. Irreversible: back up first if in
-- doubt.
--
-- Scope: impressions / clicks / views / aggregate view counts / the
-- derived ranking score / the guest ranking cache. Does NOT touch likes,
-- follows, reviews, or search history — those are relationships/content,
-- not passive tracking. See the optional block at the bottom if you want
-- those wiped too.
--
-- This does NOT touch Redis (the live per-user feed ranking cache and the
-- trending:products / trending:shops counters now live there, not in
-- Postgres — see feed/service.py, core/cache.py). Flush those separately,
-- e.g.:
--   redis-cli -u "$REDIS_URL" --scan --pattern 'feed:user:*' | xargs -r redis-cli -u "$REDIS_URL" DEL
--   redis-cli -u "$REDIS_URL" DEL feed:guest:ranked_ids trending:products trending:shops
-- or ask the assistant to run it for you via core.cache.get_redis().

BEGIN;

-- fraud_flags.event_id references listing_events with no ON DELETE rule, so
-- truncating listing_events fails unless this FK is detached first. This
-- keeps the fraud_flags rows (trust & safety history, not tracking data) —
-- it just nulls out their now-irrelevant link to the deleted raw event.
UPDATE public.fraud_flags SET event_id = NULL WHERE event_id IS NOT NULL;

-- Raw tracking logs
TRUNCATE TABLE public.listing_impressions;
TRUNCATE TABLE public.listing_events;
TRUNCATE TABLE public.analytics_events;

-- Aggregate counters derived from the logs above
UPDATE public.products SET view_count = 0, listing_score = 0;
UPDATE public.shops SET view_count = 0;

-- Guest ranking cache (Postgres fallback when Redis isn't configured;
-- harmless to clear even when Redis is the primary cache).
TRUNCATE TABLE public.feed_rank_snapshots;

COMMIT;

-- ----------------------------------------------------------------------------
-- OPTIONAL — also wipe likes / follows / search history.
-- These are relationship + content data, not passive tracking, so they're
-- NOT included above. Uncomment only if you really want a full reset.
-- ----------------------------------------------------------------------------
-- BEGIN;
-- TRUNCATE TABLE public.product_likes;
-- TRUNCATE TABLE public.shop_likes;
-- TRUNCATE TABLE public.shop_follows;
-- TRUNCATE TABLE public.search_history;
-- COMMIT;
