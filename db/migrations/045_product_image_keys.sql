-- Store UploadThing file keys next to product media URLs.
-- Videos live inside image_urls; video_keys is that subset.
-- Backfill parses /f/<key> from *.ufs.sh and utfs.io links.
-- Shops have logo_url only (no banner column). Avatars are profiles.avatar_url.
-- Run before or with the API deploy so creates persist these columns.
-- Until then the API skips the columns when Postgres says they are missing.

ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS image_keys TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS video_keys TEXT[] NOT NULL DEFAULT '{}';

UPDATE public.products AS p
SET
    image_keys = src.image_keys,
    video_keys = src.video_keys
FROM (
    SELECT
        id,
        COALESCE(
            ARRAY(
                SELECT DISTINCT substring(url FROM '/f/([^/?#]+)')
                FROM unnest(COALESCE(image_urls, ARRAY[]::text[])) AS url
                WHERE url ~* '^https://([a-z0-9-]+\.)*(ufs\.sh|utfs\.io)/f/[^/?#]+'
                  AND substring(url FROM '/f/([^/?#]+)') IS NOT NULL
            ),
            ARRAY[]::text[]
        ) AS image_keys,
        COALESCE(
            ARRAY(
                SELECT DISTINCT substring(url FROM '/f/([^/?#]+)')
                FROM unnest(COALESCE(image_urls, ARRAY[]::text[])) AS url
                WHERE url ~* '^https://([a-z0-9-]+\.)*(ufs\.sh|utfs\.io)/f/[^/?#]+'
                  AND (
                      url ~* '\.(mp4|webm|mov|m4v|avi)(\?|#|$)'
                      OR url ~* '/video/'
                  )
                  AND substring(url FROM '/f/([^/?#]+)') IS NOT NULL
            ),
            ARRAY[]::text[]
        ) AS video_keys
    FROM public.products
) AS src
WHERE p.id = src.id;
