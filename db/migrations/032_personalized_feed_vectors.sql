-- Personalized algorithm feed: pgvector math + stronger feed-cache storage.
-- Run in Supabase SQL editor after 030/031.
-- Gemini embeddings are 768-d (see feed/embeddings.py EMBEDDING_DIM).

CREATE EXTENSION IF NOT EXISTS vector;

-- Native vector column for ANN / cosine math (JSONB `embedding` kept for app compat).
ALTER TABLE public.products
  ADD COLUMN IF NOT EXISTS embedding_vec vector(768);

-- Best-effort backfill from existing JSONB arrays.
UPDATE public.products
SET embedding_vec = replace(embedding::text, ' ', '')::vector
WHERE embedding IS NOT NULL
  AND jsonb_typeof(embedding) = 'array'
  AND embedding_vec IS NULL
  AND jsonb_array_length(embedding) = 768;

-- Cosine HNSW over active published inventory only (keeps index smaller).
CREATE INDEX IF NOT EXISTS idx_products_embedding_vec_hnsw
  ON public.products
  USING hnsw (embedding_vec vector_cosine_ops)
  WHERE status = 'active'
    AND is_published = true
    AND embedding_vec IS NOT NULL;

-- Keep JSONB embedding in sync → vector on write (Python also writes both).
CREATE OR REPLACE FUNCTION public.products_sync_embedding_vec()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.embedding IS NULL THEN
    NEW.embedding_vec := NULL;
  ELSIF jsonb_typeof(NEW.embedding) = 'array'
        AND jsonb_array_length(NEW.embedding) = 768 THEN
    BEGIN
      NEW.embedding_vec := replace(NEW.embedding::text, ' ', '')::vector;
    EXCEPTION WHEN OTHERS THEN
      -- Leave previous vector if cast fails.
      NULL;
    END;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_products_sync_embedding_vec ON public.products;
CREATE TRIGGER trg_products_sync_embedding_vec
  BEFORE INSERT OR UPDATE OF embedding ON public.products
  FOR EACH ROW
  EXECUTE FUNCTION public.products_sync_embedding_vec();

-- Mathematical nearest-neighbour retrieval for personalized feed.
-- Returns lean identity rows; card hydration happens in the API after paging.
CREATE OR REPLACE FUNCTION public.match_feed_products(
  query_embedding vector(768),
  match_count integer DEFAULT 200,
  exclude_ids uuid[] DEFAULT '{}'::uuid[]
)
RETURNS TABLE (
  id uuid,
  shop_id uuid,
  similarity double precision,
  listing_score integer,
  category text,
  created_at timestamptz,
  view_count integer
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    p.id,
    p.shop_id,
    (1 - (p.embedding_vec <=> query_embedding))::double precision AS similarity,
    p.listing_score,
    p.category,
    p.created_at,
    COALESCE(p.view_count, 0)::integer AS view_count
  FROM public.products p
  WHERE p.status = 'active'
    AND p.is_published = true
    AND p.embedding_vec IS NOT NULL
    AND (exclude_ids IS NULL OR cardinality(exclude_ids) = 0 OR p.id <> ALL (exclude_ids))
  ORDER BY p.embedding_vec <=> query_embedding
  LIMIT GREATEST(1, LEAST(match_count, 500));
$$;

GRANT EXECUTE ON FUNCTION public.match_feed_products(vector, integer, uuid[]) TO service_role;
GRANT EXECUTE ON FUNCTION public.match_feed_products(vector, integer, uuid[]) TO authenticated;

-- Store ranked ID list + preference vector for 1h personalized reuse.
ALTER TABLE public.user_feed_cache
  ADD COLUMN IF NOT EXISTS preference_vector JSONB,
  ADD COLUMN IF NOT EXISTS candidate_count INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_user_feed_cache_refreshed_at
  ON public.user_feed_cache (refreshed_at DESC);

-- Fast lookup of embeddings for interaction products (user vector build).
CREATE INDEX IF NOT EXISTS idx_products_id_has_embedding
  ON public.products (id)
  WHERE embedding IS NOT NULL OR embedding_vec IS NOT NULL;
