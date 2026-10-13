-- Who uploaded which UploadThing file, so the API can safely delete
-- files a user removes before saving and sweep orphaned uploads.
-- The website records each upload from UploadThing's onUploadComplete.
-- A file is "attached" once a product, shop logo/banner or avatar uses it.

CREATE TABLE IF NOT EXISTS public.media_uploads (
    key         TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    user_id     UUID NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN (
                    'listingPhoto', 'shopLogo', 'shopBanner',
                    'verificationDoc', 'avatar', 'productVideo'
                )),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    attached_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS media_uploads_user_pending_idx
    ON public.media_uploads (user_id) WHERE attached_at IS NULL;
CREATE INDEX IF NOT EXISTS media_uploads_pending_age_idx
    ON public.media_uploads (created_at) WHERE attached_at IS NULL;

-- Only the API (service role) touches this table.
ALTER TABLE public.media_uploads ENABLE ROW LEVEL SECURITY;
