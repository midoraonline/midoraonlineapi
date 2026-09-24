-- Phase 3 — Scale / verification ladder
-- Capture (submitted) vs review queue (pending); professional badge lives in metadata.

ALTER TABLE public.shop_verifications
  DROP CONSTRAINT IF EXISTS shop_verifications_status_check;

ALTER TABLE public.shop_verifications
  ADD CONSTRAINT shop_verifications_status_check
  CHECK (status IN ('unverified', 'submitted', 'pending', 'verified', 'rejected'));

COMMENT ON COLUMN public.shop_verifications.status IS
  'Top-level queue state: submitted=docs captured (not in admin review), pending=in review queue, verified/rejected=decision.';

COMMENT ON COLUMN public.shop_verifications.metadata IS
  'Stage ladder JSON: badges[], stage2_* (identity), stage3_* (business), stage4_* (professional). Status per stage: unverified|submitted|pending|verified|rejected.';

-- Allow merchants to read their own submitted rows via existing RLS patterns if any.
DROP POLICY IF EXISTS shop_verifications_select_public ON public.shop_verifications;
