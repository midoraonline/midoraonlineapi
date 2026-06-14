-- Extend search_history for anonymous logging and search analytics.

ALTER TABLE public.search_history
  ALTER COLUMN user_id DROP NOT NULL;

ALTER TABLE public.search_history
  ADD COLUMN IF NOT EXISTS result_count INTEGER,
  ADD COLUMN IF NOT EXISTS search_mode TEXT DEFAULT 'vector'
    CHECK (search_mode IN ('vector', 'keyword', 'hybrid'));

CREATE INDEX IF NOT EXISTS idx_search_history_created_at
  ON public.search_history(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_search_history_query
  ON public.search_history(query);
