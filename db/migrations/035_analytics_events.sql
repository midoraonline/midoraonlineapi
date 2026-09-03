-- Phase 35: Generic analytics events log.
--
-- Single append-only table for platform-wide behavioral events (search,
-- listing view, WhatsApp click, favorite, follow, verification, rating,
-- report, etc). Shape matches the "one generic event" contract:
--
--   { event_type, actor_id, target_type, target_id, properties, ts, source }
--
-- New insights = new SQL over this table, not new instrumentation.
-- Derived values (interest, quality score, trust score) are recomputed on
-- demand — never stored as mutated columns here.
--
-- Existing per-listing events (`listing_events`) stay in place; this log
-- complements them (broader event surface: search, verification steps,
-- follows, ratings, reports, session-scoped joins) without duplicating
-- transactional side effects like view_count increment and feed scoring.

CREATE TABLE IF NOT EXISTS public.analytics_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type   TEXT NOT NULL,
    actor_id     UUID REFERENCES public.users(id) ON DELETE SET NULL,
    session_id   TEXT,
    target_type  TEXT,
    target_id    TEXT,
    properties   JSONB NOT NULL DEFAULT '{}'::jsonb,
    source       TEXT NOT NULL DEFAULT 'web',
    ts           TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.analytics_events ENABLE ROW LEVEL SECURITY;

-- No client-side reads: everything routes through the service role in
-- FastAPI. This prevents leaking behavioral data across actors.
DROP POLICY IF EXISTS "service role only" ON public.analytics_events;
CREATE POLICY "service role only"
    ON public.analytics_events
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Query patterns from the insights doc:
--   1. Filter by event_type + time window (all funnel/trend queries)
--   2. Filter by actor (repeat-visit, interest affinity)
--   3. Filter by target (per-shop / per-product / per-category rollups)
--   4. Session-scoped joins (search-to-contact rate)
--   5. JSONB predicates on category / hasDiscount / clickSource / etc.
CREATE INDEX IF NOT EXISTS idx_analytics_events_type_ts
    ON public.analytics_events (event_type, ts DESC);

CREATE INDEX IF NOT EXISTS idx_analytics_events_actor_ts
    ON public.analytics_events (actor_id, ts DESC)
    WHERE actor_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_analytics_events_target
    ON public.analytics_events (target_type, target_id, ts DESC)
    WHERE target_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_analytics_events_session_ts
    ON public.analytics_events (session_id, ts)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_analytics_events_properties
    ON public.analytics_events USING GIN (properties);

-- Convenience: hot-path rollup for admin trend charts. Materialize on the
-- fly instead of denormalizing counters that would drift.
CREATE OR REPLACE VIEW public.analytics_events_daily AS
SELECT
    date_trunc('day', ts) AS day,
    event_type,
    count(*)::INT AS event_count,
    count(DISTINCT actor_id) FILTER (WHERE actor_id IS NOT NULL)::INT AS unique_actors
FROM public.analytics_events
GROUP BY 1, 2;
