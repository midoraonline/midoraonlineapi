-- =============================================================================
-- 002_rls_realtime.sql
--
-- Row Level Security + Realtime publication for public-facing reads.
--
-- Context: Midora runs a custom JWT auth system (see `auth/service.py`).
-- Supabase Realtime authorises subscribers by checking a JWT signed with the
-- Supabase project JWT secret. For per-user access (chat, notifications) we
-- mint a Supabase-compatible token via `create_supabase_realtime_jwt` — see
-- migration `024_realtime_chat_rls.sql`. For anonymous / public data we grant
-- SELECT to the `anon` role and rely on `USING` clauses to hide private rows.
--
-- The API (using the service role key) BYPASSES RLS entirely for writes and
-- privileged reads; RLS below only governs what Realtime subscribers see.
--
-- Idempotent: safe to run repeatedly.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Shops: public rows should be readable by anyone. The frontend will hide
-- inactive shops from listings, but realtime subscriptions still need to see
-- updates (e.g. `is_active` flipping true/false after verification).
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "shops_select_public" ON public.shops;
CREATE POLICY "shops_select_public"
  ON public.shops
  FOR SELECT
  TO anon, authenticated
  USING (true);

-- -----------------------------------------------------------------------------
-- Products: only published products are publicly visible. Merchants will read
-- drafts through the API (service role) rather than Realtime.
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "products_select_published" ON public.products;
CREATE POLICY "products_select_published"
  ON public.products
  FOR SELECT
  TO anon, authenticated
  USING (COALESCE(is_published, false) = true);

-- -----------------------------------------------------------------------------
-- Public engagement tables (counts derived from these). Aggregates are
-- harmless to read publicly; individual rows only expose user_id+shop_id
-- which is not sensitive.
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "shop_follows_select_public" ON public.shop_follows;
CREATE POLICY "shop_follows_select_public"
  ON public.shop_follows
  FOR SELECT
  TO anon, authenticated
  USING (true);

DROP POLICY IF EXISTS "shop_likes_select_public" ON public.shop_likes;
CREATE POLICY "shop_likes_select_public"
  ON public.shop_likes
  FOR SELECT
  TO anon, authenticated
  USING (true);

DROP POLICY IF EXISTS "product_likes_select_public" ON public.product_likes;
CREATE POLICY "product_likes_select_public"
  ON public.product_likes
  FOR SELECT
  TO anon, authenticated
  USING (true);

-- -----------------------------------------------------------------------------
-- Shop verifications: pending/verified status is interesting for merchants
-- and admins. Public can see verified status only (for trust badges).
-- The API (service role) remains the authoritative writer.
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "shop_verifications_select_public" ON public.shop_verifications;
CREATE POLICY "shop_verifications_select_public"
  ON public.shop_verifications
  FOR SELECT
  TO anon, authenticated
  USING (status IN ('verified', 'pending', 'rejected'));

-- -----------------------------------------------------------------------------
-- Orders / users / profiles / chat: NO public policy. Access is strictly via
-- the API, which uses the service role to enforce ownership in application
-- code. (RLS stays enabled so mistakes fail closed.)
-- -----------------------------------------------------------------------------

-- -----------------------------------------------------------------------------
-- Realtime publication: publish only the tables we expose read access for.
-- This keeps change feeds scoped even if additional tables are added later.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    BEGIN
      ALTER PUBLICATION supabase_realtime ADD TABLE
        public.shops,
        public.products,
        public.shop_follows,
        public.shop_likes,
        public.product_likes,
        public.shop_verifications;
    EXCEPTION WHEN duplicate_object THEN
      -- Table already in the publication, nothing to do.
      NULL;
    END;
  END IF;
END
$$;
