-- Personal seller profile.
-- One hidden shop per user so a listing can be published before a real
-- storefront exists. POST /api/v1/shops upgrades this row in place
-- (same id, is_personal cleared) so products, chat, and verification stay put.
-- Do not apply from the app; run in the Supabase SQL editor.

ALTER TABLE public.shops
  ADD COLUMN IF NOT EXISTS is_personal BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.shops.is_personal IS
  'True for the auto-created seller profile used to publish without a storefront. Hidden from the public shop directory. Cleared when the owner creates a real shop (same row).';

CREATE UNIQUE INDEX IF NOT EXISTS idx_shops_one_personal_per_owner
  ON public.shops (owner_id)
  WHERE is_personal;
