-- Phase 13: Performance indexes for admin stats & reports
-- These speed up the count=exact queries, time-series bucketing,
-- and filter-based aggregations used by the admin dashboard.

-- orders: used by revenue calc (not cancelled) and time series
CREATE INDEX IF NOT EXISTS idx_orders_order_status ON public.orders(order_status);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON public.orders(created_at DESC);

-- subscriptions: used by revenue calc (payment_status filter)
CREATE INDEX IF NOT EXISTS idx_subscriptions_payment_status ON public.subscriptions(payment_status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_created_at ON public.subscriptions(created_at DESC);

-- shop_verifications: used for pending/verified/rejected counts
CREATE INDEX IF NOT EXISTS idx_shop_verifications_status ON public.shop_verifications(status);
CREATE INDEX IF NOT EXISTS idx_shop_verifications_created_at ON public.shop_verifications(requested_at DESC);

-- shops: time-series bucketing (created_at)
CREATE INDEX IF NOT EXISTS idx_shops_created_at ON public.shops(created_at DESC);

-- users: time-series bucketing and role breakdown
CREATE INDEX IF NOT EXISTS idx_users_created_at ON public.users(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_users_role ON public.users(user_role);

-- product_reports: reporter lookup (already has product_id and resolved indexes)
CREATE INDEX IF NOT EXISTS idx_product_reports_created_at ON public.product_reports(created_at DESC);

-- listing_boosts: payment_status filtering
CREATE INDEX IF NOT EXISTS idx_listing_boosts_payment_status ON public.listing_boosts(payment_status);
CREATE INDEX IF NOT EXISTS idx_listing_boosts_created_at ON public.listing_boosts(created_at DESC);
