-- Optional reason a user gives when deleting their account.
-- No FK to users: the row must survive the account being deleted.
CREATE TABLE IF NOT EXISTS public.account_deletion_reasons (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL,
    user_role   text,
    reason      text,
    details     text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS account_deletion_reasons_created_idx
    ON public.account_deletion_reasons (created_at DESC);
ALTER TABLE public.account_deletion_reasons ENABLE ROW LEVEL SECURITY;
GRANT ALL ON public.account_deletion_reasons TO service_role;
