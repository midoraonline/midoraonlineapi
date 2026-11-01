-- 060: persistent rate limits shared by all API instances and surviving restarts.
CREATE TABLE IF NOT EXISTS public.rate_limit_hits (
    id bigserial PRIMARY KEY,
    bucket text NOT NULL,
    hit_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rate_limit_hits_bucket_time
    ON public.rate_limit_hits (bucket, hit_at DESC);
ALTER TABLE public.rate_limit_hits ENABLE ROW LEVEL SECURITY;

-- Atomic check-and-record; returns seconds to wait (0 = allowed).
CREATE OR REPLACE FUNCTION public.rate_limit_hit(p_bucket text, p_limit int, p_window_seconds int)
RETURNS int
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_cutoff timestamptz := now() - make_interval(secs => p_window_seconds);
    v_count int;
    v_oldest timestamptz;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(p_bucket));
    SELECT count(*), min(hit_at) INTO v_count, v_oldest
      FROM public.rate_limit_hits WHERE bucket = p_bucket AND hit_at > v_cutoff;
    IF v_count >= p_limit THEN
        RETURN GREATEST(1, CEIL(EXTRACT(EPOCH FROM (v_oldest + make_interval(secs => p_window_seconds) - now())))::int);
    END IF;
    INSERT INTO public.rate_limit_hits (bucket) VALUES (p_bucket);
    IF random() < 0.01 THEN
        DELETE FROM public.rate_limit_hits WHERE hit_at < now() - interval '1 day';
    END IF;
    RETURN 0;
END;
$$;
REVOKE ALL ON FUNCTION public.rate_limit_hit(text, int, int) FROM PUBLIC, anon, authenticated;
