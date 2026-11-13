-- Phase 67: shop WhatsApp numbers are verified by a code sent through the WhatsApp Cloud API.
-- The wa-checker library lookup is gone, so its shared cache is no longer read or written.
-- shops.whatsapp_registered / whatsapp_public stay: shops confirmed earlier remain visible.
DROP TABLE IF EXISTS public.whatsapp_lookup_cache;
