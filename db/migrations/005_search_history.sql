-- 005_search_history.sql
-- Create table to track user searches for the algorithm feed

CREATE TABLE IF NOT EXISTS public.search_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.search_history ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_search_history_user_id ON public.search_history(user_id);
