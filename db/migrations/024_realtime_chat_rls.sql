-- =============================================================================
-- 024_realtime_chat_rls.sql
--
-- Enables per-user Supabase Realtime subscriptions for native chat.
--
-- Prerequisites (ops):
--   1. Set the Supabase project JWT secret (Studio → Project Settings → API
--      keys → JWT Secret) to the value of `APP_JWT_SECRET`. This lets Supabase
--      trust the tokens we mint via `create_supabase_realtime_jwt(user_id)`
--      (see `midoraapi/auth/service.py`).
--   2. Deploy this migration — it adds row-level SELECT policies on
--      `conversations` and `messages` bound to `auth.uid()`, and publishes
--      those tables on the `supabase_realtime` publication.
--
-- After both are in place, the browser calls `supabase.realtime.setAuth(jwt)`
-- with the token returned by `/api/v1/auth/me` and receives ONLY the
-- INSERT/UPDATE/DELETE events for conversations it participates in.
--
-- Idempotent: safe to run repeatedly.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Conversations — participants can SELECT their own rows.
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "conversations_select_participants" ON public.conversations;
CREATE POLICY "conversations_select_participants"
  ON public.conversations
  FOR SELECT
  TO authenticated
  USING (
    buyer_id = auth.uid()
    OR seller_id = auth.uid()
  );

-- -----------------------------------------------------------------------------
-- Messages — participants of the parent conversation can SELECT.
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "messages_select_participants" ON public.messages;
CREATE POLICY "messages_select_participants"
  ON public.messages
  FOR SELECT
  TO authenticated
  USING (
    EXISTS (
      SELECT 1
      FROM public.conversations c
      WHERE c.id = messages.conversation_id
        AND (c.buyer_id = auth.uid() OR c.seller_id = auth.uid())
    )
  );

-- -----------------------------------------------------------------------------
-- REPLICA IDENTITY FULL on messages so UPDATE events (read_at flips) carry
-- the full row payload — otherwise Realtime clients only see primary keys
-- and cannot reconcile message state without an extra fetch.
-- -----------------------------------------------------------------------------
ALTER TABLE public.messages REPLICA IDENTITY FULL;
ALTER TABLE public.conversations REPLICA IDENTITY FULL;

-- -----------------------------------------------------------------------------
-- Publish chat tables on the realtime publication.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    BEGIN
      ALTER PUBLICATION supabase_realtime ADD TABLE
        public.conversations,
        public.messages;
    EXCEPTION WHEN duplicate_object THEN
      -- Already published — nothing to do.
      NULL;
    END;
  END IF;
END
$$;
