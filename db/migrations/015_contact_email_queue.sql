-- Phase 15: Contact submissions & email queue

-- 1. Contact form submissions
CREATE TABLE IF NOT EXISTS public.contact_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_contact_submissions_created
    ON public.contact_submissions(created_at DESC);

-- 2. Email queue for async sending with retry
CREATE TABLE IF NOT EXISTS public.mail_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_html TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'failed')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    retries INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mail_queue_pending
    ON public.mail_queue(status, created_at)
    WHERE status = 'pending';
