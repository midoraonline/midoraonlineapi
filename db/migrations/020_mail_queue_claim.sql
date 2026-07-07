-- Atomic mail queue claiming so multiple API workers cannot send the same email twice.

ALTER TABLE public.mail_queue DROP CONSTRAINT IF EXISTS mail_queue_status_check;
ALTER TABLE public.mail_queue ADD CONSTRAINT mail_queue_status_check
    CHECK (status IN ('pending', 'processing', 'sent', 'failed'));

CREATE OR REPLACE FUNCTION public.claim_next_mail_queue_item()
RETURNS SETOF public.mail_queue
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  UPDATE public.mail_queue
  SET status = 'processing'
  WHERE id = (
    SELECT id FROM public.mail_queue
    WHERE status = 'pending'
    ORDER BY created_at
    LIMIT 1
    FOR UPDATE SKIP LOCKED
  )
  RETURNING *;
END;
$$;
