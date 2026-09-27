# Unused schema audit

Read-only review of the migration schema against the API and the frontend. Nothing here was applied to production.

- API: `midoraonline/midoraonlineapi` `main` @ `366b547`
- Frontend: `midoraonline/midoraonline` `main` @ `a48a9ed` (cloned read-only; it does not query Postgres except Supabase Realtime)
- Schema built by applying `supabase_schema.sql` and `db/migrations/001`–`046` on local Postgres 16 (46 base tables, 2 views, 1 trigger)

Searches covered Python, SQL (functions, triggers, views, RLS, seeds), and frontend TypeScript. Cron in this repo is HTTP (`/moderation/drain`, `/media/sweep`), not `pg_cron` jobs that name these tables.

Classification is conservative. Auth, Pesapal payments, refresh tokens, reviews, moderation, and migrations 043–046 stay **used** unless a column is never read and never written.

## Proposed removals (unused)

| Object | Reason | Evidence | Risk |
| --- | --- | --- | --- |
| `public.listing_images` (`id`, `listing_id`, `image_url`, `sort_order`, `created_at`) | Superseded by `products.image_urls` (and `image_keys` / `video_keys` from migration 045). | Only `supabase_schema.sql` and `db/migrations/010_new_tables.sql`. No `table("listing_images")`, no frontend reference, no trigger, no seed. RLS is enabled and no policy was added. | Low. Confirm `estimated_rows` before dropping. Rows are copied to `archive.listing_images` first. |
| `subscriptions.payment_method` | Column is declared and never read or written. Live Pesapal rows use `payment_status`, `merchant_reference`, `pesapal_order_tracking_id`, `plan_tier`, `amount`, `currency`. | Only `supabase_schema.sql` (the `CREATE TABLE`). No Python, frontend, function, or seed reference. | Medium, because the table is the Pesapal ledger. The table is not dropped. Backup is `archive.subscriptions_payment_method`. Skip this column if any non-null values show up in the live check. |
| `subscriptions.ipn_id` | The API talks to Pesapal's IPN registry in memory (`payments/service.py` `_get_ipn_id`). It never inserts or selects `subscriptions.ipn_id`. | Word `ipn_id` in `payments/service.py` is the Pesapal JSON field (`row["ipn_id"]` on their IPN list, `res.json().get("ipn_id")`), not this column. Inserts and selects on `subscriptions` do not include it. | Medium, same as above. Backup is `archive.subscriptions_ipn_id`. |

`db/audit/cleanup_unused_schema.sql` archives then drops only these three. It is one `DO` statement and is not a numbered migration.

## Write-only / legacy (not in the cleanup script)

| Object | Why it stays | Evidence | Risk if dropped |
| --- | --- | --- | --- |
| `refresh_tokens.issued_at` | Auth. Default `now()` fills it on insert. Rotation reads the row with `select("*")` but never uses this field. | Only `db/migrations/001_refresh_tokens.sql`. `auth/service.py` does not set or read it. Migration 044 `family_id` is the live reuse key. | High. Leave it. |
| `refresh_tokens.replaced_by` | Auth. Written on revoke/rotate, never read back. | `auth/service.py` `_insert_refresh_record` / `_revoke_refresh_record`. | High. Leave it. |
| `push_subscriptions.last_used_at` | Default `now()` on insert. Push send does not update it and nothing reads it. | Only `supabase_schema.sql` and `025_push_subscriptions.sql`. `notifications/push_service.py` writes `endpoint`, `p256dh`, `auth`, `user_agent`. | Low, but it is write-only, so it is not in the first cleanup. |

`products.embedding` (jsonb) and `products.embedding_vec` are both live: the API writes the jsonb (`feed/embeddings.py`) and trigger `trg_products_sync_embedding_vec` copies it for `match_feed_products`. Not a dead pair.

`products.image_urls` is the listing gallery. `image_keys` / `video_keys` (045) are the UploadThing keys for that same array. `listing_images` is the unused side table.

`users` and `profiles` both store `full_name`, `phone_number`, and `user_role`. Auth reads and writes both (`auth/providers/emailpassword.py`). Not safe to drop either copy.

## Borderline, left out of the cleanup

| Object | Why it was left out | Evidence | Risk |
| --- | --- | --- | --- |
| `public.orders` (`customer_id`, `shop_id`, `total_amount`, `order_status`, `pesapal_tracking_id`, …) | No application reads or writes. Admin stats say checkout orders are not part of the product and sum `subscriptions` instead (`admin/routes/stats.py`). `013_admin_perf_indexes.sql` still indexes `order_status` from an older revenue plan. The `pesapal_tracking_id` name is payment-shaped, so this stays until the live check shows it is empty and Joel confirms no external writer. | `table("orders")` does not exist. Frontend has no `orders` query. Pesapal code uses `subscriptions` and `pesapal_webhook_logs`. | High if dropped while rows exist. Not in `cleanup_unused_schema.sql`. |
| `get_or_create_conversation`, `calculate_shop_duration`, `submit_verification_with_docs` | SQL functions with no API or frontend caller. Chat and verification were reimplemented in Python (`marketplace/routes/chat_native.py`, `tenants/routes/verifications.py`). | Definitions only: `012_native_chat.sql`, `011_verification_docs_comments.sql`. | Medium. Functions are not tables; this pass does not drop them. |

## Used tables

Every other public table is read or written by the API, a SQL function the API calls, or frontend Realtime. Migrations 043–046 are in use: `shops.is_personal`, `refresh_tokens.family_id`, `products.image_keys`, `products.video_keys`, `categories.parent_slug`, `categories.metadata`.

| Table | Last reference |
| --- | --- |
| `analytics_events` | `analytics/routes/insights.py` (view `analytics_events_daily` too) |
| `boost_plans` | `ranking/boost_service.py` |
| `categories` | `categories/service.py`; metadata filled by `db/seeds/seed_services_and_opportunities.sql` |
| `contact_submissions` | `mail/routes/contactus.py` |
| `conversations` | `marketplace/routes/chat_native.py`; frontend Realtime `components/chat/ChatList.tsx` |
| `email_verification_tokens` | `auth/providers/emailpassword.py` |
| `feed_config` | `feed/config.py`, `admin/routes/feed_config.py` |
| `fraud_flags` | `ranking/fraud_service.py`, `admin/routes/fraud.py` |
| `lead_events` | `ranking/lead_service.py` |
| `listing_boosts` | `ranking/boost_service.py`, `feed/composite.py` |
| `listing_events` | `marketplace/routes/listing_events.py`; `whatsapp_clicks` on cards is counted from here, not a products column |
| `listing_image_hashes` | `listingModeration/stages/near_duplicate.py` (`_HASHES_TABLE`) |
| `listing_impressions` | `feed/impressions.py` (view `v_listing_impressions_agg`) |
| `listing_moderation_queue` | `listingModeration/service.py`; cron drain calls `claim_moderation_queue_batch` |
| `mail_queue` | `mail/queue.py` (`claim_next_mail_queue_item`) |
| `messages` | `marketplace/routes/chat_native.py`; frontend Realtime `components/chat/ChatThread.tsx` |
| `moderation_bad_image_hashes` | `listingModeration/service.py` (`_BAD_HASHES_TABLE`) |
| `notifications` | `notifications/` |
| `online_presence` | `marketplace/presence_service.py` |
| `pesapal_webhook_logs` | `payments/service.py` (insert payload, set `processed`) |
| `platform_feedback` | `marketplace/routes/feedback.py`, `admin/routes/feedback.py` |
| `product_comments` | `reviews/` and admin comment routes; frontend admin flag toggle |
| `product_likes` | `shop/engagement_service.py` |
| `product_reports` | `marketplace/routes/reports.py` |
| `product_reposts_log` | `shop/service.py` (read today's rows, then insert) |
| `product_reviews` | `feed/catalog.py` `rating_map`, `reviews/product_service.py` |
| `products` | Feed, shop, search, moderation. Card ratings are computed from `product_reviews`, not a products column. |
| `profiles` | `auth/providers/emailpassword.py` (`avatar_url` lives here) |
| `push_subscriptions` | `notifications/push_service.py` |
| `refresh_tokens` | `auth/service.py` (migration 044 `family_id`) |
| `search_history` | `search/service.py` |
| `seller_blocks` | `marketplace/routes/reports.py` |
| `seller_reports` | `marketplace/routes/reports.py` |
| `seller_reviews` | `reviews/service.py`; `recalculate_shop_seller_score` |
| `shop_ai_context` | `ai/routes/context.py`, `ai/tools/supabase_tools.py` |
| `shop_comments` | shop comment routes; frontend admin flag toggle |
| `shop_follows` | `shop/engagement_service.py` |
| `shop_likes` | `shop/engagement_service.py` |
| `shop_verifications` | `tenants/routes/verifications.py`; frontend Realtime |
| `shops` | Tenants, feed, payments (`subscription_end_date`). `is_personal` is migration 043. |
| `subscriptions` | `payments/service.py` (Pesapal). Only `payment_method` and `ipn_id` are unused columns. |
| `user_feed_cache` | `feed/service.py` (`ranked_ids`, `preference_vector`, `candidate_count`) |
| `users` | Auth, plans (`plan_tier`, `plan_expires_at`), phone verification |
| `verification_codes` | `common/verification_service.py` |

Frontend Realtime subscribes to `shops`, `products`, `shop_verifications`, `messages`, and `conversations`. Other screens go through the API.

## Views, trigger, functions

- `analytics_events_daily`: read in `analytics/routes/insights.py`. Used.
- `v_listing_impressions_agg`: read in `feed/impressions.py`. Used.
- `trg_products_sync_embedding_vec`: keeps `embedding_vec` aligned with `embedding`. Used.
- Called from Python: `match_feed_products`, `recalculate_product_listing_score`, `recalculate_shop_seller_score`, `reclaim_stuck_moderation_rows`, `claim_moderation_queue_batch`, `increment_unread`, `increment_product_view_count`, `increment_shop_view_count`, `claim_next_mail_queue_item`.
- Defined and not called: `get_or_create_conversation`, `calculate_shop_duration`, `submit_verification_with_docs`. Left in place (see borderline).

## How to run the live check first

1. Open `db/audit/list_live_schema.sql`, paste it into the Supabase SQL Editor, and run it.
2. Diff `table_name` / `column_name` against this report. Missing rows mean a migration was not applied.
3. For `listing_images`, `subscriptions.payment_method`, and `subscriptions.ipn_id`, read `estimated_rows` and `estimated_non_null`. Do not run the cleanup if those estimates are non-zero and unexpected, or if `estimated_non_null` is NULL because the table was never analyzed.
4. Only then, and only by hand, run `db/audit/cleanup_unused_schema.sql` as one query. It writes `archive.listing_images`, `archive.subscriptions_payment_method`, and `archive.subscriptions_ipn_id`, then drops the unused objects.
