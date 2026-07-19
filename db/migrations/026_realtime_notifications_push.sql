-- =============================================================================
-- 026_realtime_notifications_push.sql
--
-- Enables per-user Supabase Realtime subscriptions for in-app notifications
-- and pins the `push_subscriptions` table's ownership policy. Depends on
-- migration 024 (which set up the Supabase-compatible JWT auth path).
--
-- Idempotent: safe to run repeatedly.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Notifications — recipients can SELECT their own notifications.
-- -----------------------------------------------------------------------------
DROP POLICY IF EXISTS "notifications_select_owner" ON public.notifications;
CREATE POLICY "notifications_select_owner"
  ON public.notifications
  FOR SELECT
  TO authenticated
  USING (user_id = auth.uid());

-- Full-row payload so UPDATE (status flip to "read") includes user_id in the
-- old-row, which Realtime clients use to route the event.
ALTER TABLE public.notifications REPLICA IDENTITY FULL;

-- -----------------------------------------------------------------------------
-- Publish on the realtime publication.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    BEGIN
      ALTER PUBLICATION supabase_realtime ADD TABLE public.notifications;
    EXCEPTION WHEN duplicate_object THEN
      NULL;
    END;
  END IF;
END
$$;

-- -----------------------------------------------------------------------------
-- push_subscriptions — closed to anon/authenticated; service role manages it.
-- (RLS is already enabled by migration 025; this migration just documents
-- that we intentionally have NO SELECT/INSERT/UPDATE/DELETE policy for
-- either role.)
-- -----------------------------------------------------------------------------
