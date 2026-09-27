-- Cleanup against the production inventory Joel captured with list_live_schema.sql.
-- NOT a numbered migration. Do not run this from deploy.
--
-- One DO statement. No temp tables. Archive first, then drop. IF EXISTS /
-- catalog checks skip anything production does not have. Re-running is safe.
-- The statement rolls back if a step fails.
--
-- Drops only objects with no reads and no writes in the API or frontend:
--   public.orders
--   products.average_rating, products.rating_count
--   chat_messages.thought_signature
--   get_or_create_conversation(uuid, uuid, uuid, uuid)
--   calculate_shop_duration(uuid)
--   submit_verification_with_docs(uuid, text, jsonb, text, text, text)
--
-- Does not drop listing_images, subscriptions.payment_method, or
-- subscriptions.ipn_id: those are not in this production database.

DO $cleanup$
DECLARE
    func_args text;
BEGIN
    CREATE SCHEMA IF NOT EXISTS archive;

    IF to_regclass('public.orders') IS NOT NULL THEN
        EXECUTE 'DROP TABLE IF EXISTS archive.orders';
        EXECUTE 'CREATE TABLE archive.orders AS SELECT * FROM public.orders';
        EXECUTE 'DROP TABLE public.orders';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'products' AND column_name = 'average_rating'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'products' AND column_name = 'rating_count'
    ) THEN
        EXECUTE 'DROP TABLE IF EXISTS archive.products_rating_columns';
        EXECUTE $sql$
            CREATE TABLE archive.products_rating_columns AS
            SELECT id, average_rating, rating_count FROM public.products
        $sql$;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'products' AND column_name = 'average_rating'
    ) THEN
        EXECUTE 'ALTER TABLE public.products DROP COLUMN average_rating';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'products' AND column_name = 'rating_count'
    ) THEN
        EXECUTE 'ALTER TABLE public.products DROP COLUMN rating_count';
    END IF;

    IF to_regclass('public.chat_messages') IS NOT NULL
       AND EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'chat_messages'
               AND column_name = 'thought_signature'
       ) THEN
        EXECUTE 'DROP TABLE IF EXISTS archive.chat_messages_thought_signature';
        EXECUTE $sql$
            CREATE TABLE archive.chat_messages_thought_signature AS
            SELECT id, thought_signature FROM public.chat_messages
        $sql$;
        EXECUTE 'ALTER TABLE public.chat_messages DROP COLUMN thought_signature';
    END IF;

    -- Drop only the signatures defined in migrations 011 and 012.
    SELECT pg_get_function_identity_arguments(p.oid)
      INTO func_args
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.proname = 'get_or_create_conversation'
     LIMIT 1;
    IF func_args = 'p_buyer_id uuid, p_seller_id uuid, p_shop_id uuid, p_product_id uuid' THEN
        EXECUTE 'DROP FUNCTION IF EXISTS public.get_or_create_conversation(uuid, uuid, uuid, uuid)';
    ELSIF func_args IS NOT NULL THEN
        RAISE NOTICE 'left get_or_create_conversation(%); signature differs', func_args;
    END IF;

    SELECT pg_get_function_identity_arguments(p.oid)
      INTO func_args
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.proname = 'calculate_shop_duration'
     LIMIT 1;
    IF func_args = 'p_shop_id uuid' THEN
        EXECUTE 'DROP FUNCTION IF EXISTS public.calculate_shop_duration(uuid)';
    ELSIF func_args IS NOT NULL THEN
        RAISE NOTICE 'left calculate_shop_duration(%); signature differs', func_args;
    END IF;

    SELECT pg_get_function_identity_arguments(p.oid)
      INTO func_args
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.proname = 'submit_verification_with_docs'
     LIMIT 1;
    IF func_args = 'p_shop_id uuid, p_notes text, p_submitted_docs jsonb, p_submitted_phone text, p_submitted_whatsapp text, p_submitted_location text' THEN
        EXECUTE 'DROP FUNCTION IF EXISTS public.submit_verification_with_docs(uuid, text, jsonb, text, text, text)';
    ELSIF func_args IS NOT NULL THEN
        RAISE NOTICE 'left submit_verification_with_docs(%); signature differs', func_args;
    END IF;
END
$cleanup$;
