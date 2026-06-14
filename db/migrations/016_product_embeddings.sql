-- Semantic product embeddings for personalized feed ranking.
-- Stored as JSONB (float array) for compatibility with the Supabase Python client.

ALTER TABLE public.products
  ADD COLUMN IF NOT EXISTS embedding JSONB,
  ADD COLUMN IF NOT EXISTS embedding_source_hash TEXT,
  ADD COLUMN IF NOT EXISTS embedding_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_products_has_embedding
  ON public.products (embedding_updated_at DESC NULLS LAST)
  WHERE embedding IS NOT NULL;
