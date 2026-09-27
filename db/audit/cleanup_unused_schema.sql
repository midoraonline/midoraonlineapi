-- First-step cleanup for objects classified unused in unused_schema_report.md.
-- NOT a numbered migration. Do not run this on deploy.
--
-- Run db/audit/list_live_schema.sql first and read the row estimates.
-- This script copies each object into schema archive, then drops it.
-- One DO statement, so a pooled SQL editor cannot split it across connections.
-- The whole statement rolls back if any step fails.
-- Safe to re-run: objects already dropped are skipped. Archive tables are
-- replaced with a fresh copy when the source still exists.
--
-- Included:
--   public.listing_images (superseded by products.image_urls; no app usage)
--   subscriptions.payment_method (defined, never read or written)
--   subscriptions.ipn_id (Pesapal IPN id is not stored on this column)
-- The subscriptions table itself is not dropped.

DO $cleanup$
BEGIN
    CREATE SCHEMA IF NOT EXISTS archive;

    IF to_regclass('public.listing_images') IS NOT NULL THEN
        EXECUTE 'DROP TABLE IF EXISTS archive.listing_images';
        EXECUTE 'CREATE TABLE archive.listing_images AS SELECT * FROM public.listing_images';
        EXECUTE 'DROP TABLE public.listing_images';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'subscriptions'
           AND column_name = 'payment_method'
    ) THEN
        EXECUTE 'DROP TABLE IF EXISTS archive.subscriptions_payment_method';
        EXECUTE $sql$
            CREATE TABLE archive.subscriptions_payment_method AS
            SELECT id, payment_method FROM public.subscriptions
        $sql$;
        EXECUTE 'ALTER TABLE public.subscriptions DROP COLUMN payment_method';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'subscriptions'
           AND column_name = 'ipn_id'
    ) THEN
        EXECUTE 'DROP TABLE IF EXISTS archive.subscriptions_ipn_id';
        EXECUTE $sql$
            CREATE TABLE archive.subscriptions_ipn_id AS
            SELECT id, ipn_id FROM public.subscriptions
        $sql$;
        EXECUTE 'ALTER TABLE public.subscriptions DROP COLUMN ipn_id';
    END IF;
END
$cleanup$;
