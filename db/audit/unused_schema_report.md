# Production schema cleanup

Joel ran `db/audit/list_live_schema.sql` on production. This report diffs that inventory against the migrations and against code on:

- API: `midoraonline/midoraonlineapi` `main` @ `8ba0a24`
- Frontend: `midoraonline/midoraonline` `main` @ `a48a9ed`

The first cleanup (PR #10) targeted `listing_images`, `subscriptions.payment_method`, and `subscriptions.ipn_id`. Those objects are not in this production database, so that script is a no-op here. This pass drops only objects that are in the live inventory and have no reads or writes.

Nothing in this file was applied to production.

## Rating columns

Drop `products.average_rating` and `products.rating_count`. Do not add a sync trigger.

Production has both columns on all 71 products, `numeric(3,2)` and `int`, default `0`. There are 14 `product_reviews` rows, and every product is still `0`, so the columns were never maintained. They are not in any migration. Card ratings are computed in Python from `product_reviews.rating` (`feed/catalog.py` `card_rating` / `rating_map`) and returned as `average_rating` (number or null) and `review_count`. The card select in `feed/service.py` does not include the stored columns. `shop/service.py` `get_product` loads `select("*")` and then overwrites `average_rating` from `product_reviews`. The frontend reads the JSON fields `average_rating` and `review_count` (`lib/cardRating.ts`); it does not read `rating_count`. No view or SQL function in this repo names either column.

Blank stars on the old home feed were the API omitting the review query and sending `0`, which the card treats as unrated. They were not caused by reading `products.average_rating`. A trigger would keep a column the API ignores, and a stored `0` still looks unrated.

## What production is missing

In the repo DDL, absent from the live inventory:

| Object | Where it is defined | Note |
| --- | --- | --- |
| `public.listing_images` | `supabase_schema.sql`, `db/migrations/010_new_tables.sql` | Superseded by `products.image_urls`. Not in production, so this cleanup does not touch it. |
| `subscriptions.payment_method` | `supabase_schema.sql` `CREATE TABLE` only | Never a later migration. Pesapal writes `payment_status`, `merchant_reference`, `pesapal_order_tracking_id`, `plan_tier`, `amount`, `currency`. |
| `subscriptions.ipn_id` | `supabase_schema.sql` `CREATE TABLE` only | The API keeps Pesapal's IPN id in memory (`payments/service.py`). It does not select this column. |

Migrations that the inventory shows as applied: `categories.parent_slug` and `categories.metadata` (046; 186 of 206 categories have a parent), `products.image_keys` / `video_keys` (045), `refresh_tokens.family_id` (044), `shops.is_personal` (043), `products.embedding_vec` (kept in sync by `trg_products_sync_embedding_vec`).

## What production has that migrations do not

| Object | Rows | Decision |
| --- | --- | --- |
| `chat_sessions` | 245 | Keep. `ai/routes/chat.py` reads and writes it. No `CREATE TABLE` in the repo, so a fresh database would not have the AI concierge tables. Not dropped. |
| `chat_messages` | 172 | Keep the table. Same writer. |
| `chat_messages.thought_signature` | 0 non-null | Drop. No Python or frontend reference. Inserts are `session_id`, `sender_type`, `message`. |
| `products.average_rating`, `products.rating_count` | all 71 at default 0 | Drop. See above. |
| `product_reviews.updated_at` | present | Leave. Not in the repo DDL and not in the removal list. Review code does not name it. |

## Removals

`db/audit/cleanup_unused_schema.sql` is one `DO` statement. It creates `archive` if needed, copies rows, then drops. Catalog checks skip anything production does not have. A failed step rolls the statement back. It is not a numbered migration.

| Object | Reason |
| --- | --- |
| `public.orders` | 0 rows. No `table("orders")` in the API. Frontend has no orders query. Admin stats sum `subscriptions` and say checkout orders are not part of the product (`admin/routes/stats.py`). Only `supabase_schema.sql` and an index in `013_admin_perf_indexes.sql`. |
| `products.average_rating`, `products.rating_count` | Stale defaults. Cards compute ratings from `product_reviews`. No SQL view references them. |
| `chat_messages.thought_signature` | All null. No code reads or writes the name. |
| `get_or_create_conversation(uuid, uuid, uuid, uuid)` | Defined in `012_native_chat.sql`. Chat is `marketplace/routes/chat_native.py`. No caller. |
| `calculate_shop_duration(uuid)` | Defined in `011_verification_docs_comments.sql`. No caller. |
| `submit_verification_with_docs(uuid, text, jsonb, text, text, text)` | Same migration. Verification is `tenants/routes/verifications.py`. No caller. |

The script drops a function only when `pg_get_function_identity_arguments` matches that signature, then `DROP FUNCTION IF EXISTS`. A different signature is left in place and a `NOTICE` is raised. `increment_unread` stays.

Archive tables: `archive.orders`, `archive.products_rating_columns` (`id`, `average_rating`, `rating_count`), `archive.chat_messages_thought_signature` (`id`, `thought_signature`).

## Empty in production, and staying

These are empty or all-null in the live inventory. Application code still reads or writes them.

| Object | Why it stays |
| --- | --- |
| `products.ai_seo_tags` | Selected on product detail (`shop/service.py`) and included in the embedding source hash (`feed/embeddings.py`). Tags are not generated yet. |
| `products.discount_expires_at` | On card, search, and shop selects. Product update writes it (`shop/routes/products.py`). |
| `listing_events.ip_address`, `listing_events.device_hash` | `marketplace/routes/listing_events.py` inserts both from query params. Clients are omitting them today (`session_id` is only 2 non-null). |
| `listing_impressions.device_hash` | `feed/impressions.py` sets it on insert. `analytics/materialize.py` writes null. |
| `users.plan_expires_at` | `payments/plan_service.py` and `payments/service.py` read and write it. Auth session returns it. No expiry has been stored yet. |
| `shops.subscription_end_date` | `payments/service.py` writes it. Feed scoring, placement, and shop reads use it. |
| `online_presence` | `marketplace/presence_service.py` upserts and deletes stale rows. |
| `fraud_flags` | `ranking/fraud_service.py`, `admin/routes/fraud.py`. |
| `listing_boosts` | `ranking/boost_service.py`, `feed/composite.py`, shop and admin reads. |
| `moderation_bad_image_hashes` | `listingModeration/service.py` (`_BAD_HASHES_TABLE`). |
| `pesapal_webhook_logs` | `payments/service.py` inserts the IPN payload and sets `processed`. |
| `seller_blocks`, `seller_reports` | `marketplace/routes/reports.py`, `admin/routes/reports.py`. |
| `shop_comments` | `marketplace/routes/comments.py` and the admin flag toggle. |

## Still not dropped

| Object | Why |
| --- | --- |
| `refresh_tokens.issued_at` | Default `now()` on insert. Rotation uses `select("*")` and does not read the field. Auth table. |
| `refresh_tokens.replaced_by` | Written on rotate, never read. Auth table. |
| `push_subscriptions.last_used_at` | Default `now()`. Push send writes `endpoint`, `p256dh`, `auth`, `user_agent` only. |
| `chat_sessions`, `chat_messages` | Live AI concierge. Missing from migrations; do not drop. |
| `product_reviews.updated_at` | Extra versus migrations. Review code does not use it. Left alone. |

`products.embedding` (jsonb) and `products.embedding_vec` are both live. `users` and `profiles` both store name, phone, and role; auth writes both.

## How to run

1. Paste `db/audit/cleanup_unused_schema.sql` into the Supabase SQL Editor and run it once. It is a single statement, so the pooled editor keeps the work inside one transaction.
2. Expect notices only if a function exists under a different signature. Those functions are left alone.
3. Running it again is a no-op: each drop is guarded by a catalog check.
4. Rows are in `archive.orders`, `archive.products_rating_columns`, and `archive.chat_messages_thought_signature` before the drop. `orders` is empty in the inventory, so that archive table will be empty.
5. Do not run this from deploy. It is not a numbered migration.
