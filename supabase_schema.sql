-- Midora Database Schema — run in Supabase SQL Editor
-- Requires pgcrypto for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ═══════════════════════════════════════════════════════════════════════════
-- 1. USERS & AUTH
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    full_name       TEXT,
    user_role       TEXT NOT NULL DEFAULT 'customer'
                        CHECK (user_role IN ('merchant', 'customer', 'admin', 'staff')),
    email_verified  BOOLEAN NOT NULL DEFAULT false,
    phone_number    TEXT UNIQUE,
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'suspended', 'blocked')),
    last_seen_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_users_email ON public.users(email);

CREATE TABLE IF NOT EXISTS public.email_verification_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    token       TEXT NOT NULL UNIQUE,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.email_verification_tokens ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_email_verification_tokens_user_id
    ON public.email_verification_tokens(user_id);

CREATE TABLE IF NOT EXISTS public.profiles (
    id          UUID PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
    full_name   TEXT,
    avatar_url  TEXT,
    phone_number TEXT UNIQUE,
    user_role   TEXT CHECK (user_role IN ('merchant', 'customer', 'admin', 'staff'))
                    DEFAULT 'customer',
    created_at  TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- ═══════════════════════════════════════════════════════════════════════════
-- 2. SHOPS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.shops (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id            UUID NOT NULL REFERENCES public.users(id),
    name                TEXT NOT NULL,
    slug                TEXT UNIQUE NOT NULL,
    description         TEXT,
    about               TEXT,
    logo_url            TEXT,
    shop_email          TEXT,
    whatsapp_number     TEXT,
    contacts            JSONB NOT NULL DEFAULT '[]'::jsonb,
    social_links        JSONB NOT NULL DEFAULT '[]'::jsonb,
    location            JSONB,
    availability        JSONB,
    theme_config        JSONB DEFAULT '{"primary_color":"#000000","background_color":"#ffffff","text_color":"#111111","font":"Inter","theme":"default","metadata":{}}'::jsonb,
    shop_type           TEXT NOT NULL DEFAULT 'product'
                            CHECK (shop_type IN ('product', 'service', 'both')),
    category            TEXT,
    is_active           BOOLEAN DEFAULT false,
    subscription_end_date TIMESTAMPTZ,
    view_count          BIGINT NOT NULL DEFAULT 0,
    trust_score         NUMERIC(3,2) NOT NULL DEFAULT 0.00,
    fraud_score         NUMERIC(3,2) NOT NULL DEFAULT 0.00,
    seller_score        NUMERIC(6,2) NOT NULL DEFAULT 0.00,
    available_now       BOOLEAN NOT NULL DEFAULT false,
    last_seen_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.shops ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_shops_owner_id ON public.shops(owner_id);
CREATE INDEX IF NOT EXISTS idx_shops_slug ON public.shops(slug);
CREATE INDEX IF NOT EXISTS idx_shops_shop_type ON public.shops(shop_type);

CREATE TABLE IF NOT EXISTS public.shop_verifications (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id             UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    status              TEXT NOT NULL DEFAULT 'unverified'
                            CHECK (status IN ('unverified', 'pending', 'verified', 'rejected')),
    requested_at        TIMESTAMPTZ DEFAULT now(),
    reviewed_at         TIMESTAMPTZ,
    reviewed_by         UUID REFERENCES public.users(id),
    notes               TEXT,
    metadata            JSONB,
    submitted_docs      JSONB DEFAULT '{}'::jsonb,
    submitted_phone     TEXT,
    submitted_whatsapp  TEXT,
    submitted_location  TEXT,
    shop_duration_days  INTEGER DEFAULT 0
);
ALTER TABLE public.shop_verifications ENABLE ROW LEVEL SECURITY;
CREATE UNIQUE INDEX IF NOT EXISTS idx_shop_verifications_shop_id_unique
    ON public.shop_verifications(shop_id);

-- ═══════════════════════════════════════════════════════════════════════════
-- 3. SHOP ENGAGEMENT (follows / likes)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.shop_follows (
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    shop_id     UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, shop_id)
);
ALTER TABLE public.shop_follows ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_shop_follows_shop_id ON public.shop_follows(shop_id);

CREATE TABLE IF NOT EXISTS public.shop_likes (
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    shop_id     UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, shop_id)
);
ALTER TABLE public.shop_likes ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_shop_likes_shop_id ON public.shop_likes(shop_id);

-- ═══════════════════════════════════════════════════════════════════════════
-- 4. PRODUCTS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.products (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id         UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    item_type       TEXT NOT NULL DEFAULT 'product'
                        CHECK (item_type IN ('product', 'service', 'property', 'job')),
    title           TEXT NOT NULL,
    description     TEXT,
    price_ugx       NUMERIC(12,2) NOT NULL,
    stock_quantity  INTEGER DEFAULT 0,
    image_urls      TEXT[] DEFAULT '{}'::text[],
    category        TEXT,
    ai_seo_tags     TEXT,
    ai_generated_desc BOOLEAN DEFAULT false,
    is_published    BOOLEAN DEFAULT true,
    view_count      BIGINT NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('draft', 'pending_review', 'active', 'hidden', 'rejected', 'expired', 'sold')),
    listing_score   INTEGER NOT NULL DEFAULT 0,
    location_name   TEXT,
    reviewed_by     UUID REFERENCES public.users(id),
    reviewed_at     TIMESTAMPTZ,
    review_notes    TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_products_shop_id ON public.products(shop_id);
CREATE INDEX IF NOT EXISTS idx_products_created_at ON public.products(created_at DESC);

CREATE TABLE IF NOT EXISTS public.product_likes (
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    product_id  UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, product_id)
);
ALTER TABLE public.product_likes ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_product_likes_product_id ON public.product_likes(product_id);

CREATE TABLE IF NOT EXISTS public.product_reposts_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id  UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.product_reposts_log ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_product_reposts_log_product_id
    ON public.product_reposts_log(product_id);

CREATE TABLE IF NOT EXISTS public.listing_images (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id  UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    image_url   TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.listing_images ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_listing_images_listing_id
    ON public.listing_images(listing_id);
CREATE INDEX IF NOT EXISTS idx_listing_images_sort
    ON public.listing_images(listing_id, sort_order);

CREATE TABLE IF NOT EXISTS public.listing_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id  UUID REFERENCES public.products(id) ON DELETE CASCADE,
    seller_id   UUID REFERENCES public.users(id),
    buyer_id    UUID REFERENCES public.users(id),
    session_id  TEXT,
    event_type  TEXT NOT NULL CHECK (event_type IN (
                    'viewed', 'whatsapp_clicked', 'call_clicked', 'saved',
                    'shared', 'reported', 'updated', 'messaged'
                )),
    ip_address  TEXT,
    device_hash TEXT,
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.listing_events ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_listing_events_listing_id
    ON public.listing_events(listing_id);
CREATE INDEX IF NOT EXISTS idx_listing_events_seller_id
    ON public.listing_events(seller_id);
CREATE INDEX IF NOT EXISTS idx_listing_events_buyer_id
    ON public.listing_events(buyer_id);
CREATE INDEX IF NOT EXISTS idx_listing_events_event_type
    ON public.listing_events(event_type);
CREATE INDEX IF NOT EXISTS idx_listing_events_created_at
    ON public.listing_events(created_at DESC);

CREATE TABLE IF NOT EXISTS public.lead_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id  UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    seller_id   UUID NOT NULL REFERENCES public.users(id),
    buyer_id    UUID REFERENCES public.users(id),
    source      TEXT NOT NULL CHECK (source IN ('whatsapp', 'call', 'contact_form', 'email')),
    lead_status TEXT NOT NULL DEFAULT 'new'
                    CHECK (lead_status IN ('new', 'responded', 'ignored', 'closed')),
    unique_key  TEXT UNIQUE NOT NULL,
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.lead_events ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_lead_events_listing_id ON public.lead_events(listing_id);
CREATE INDEX IF NOT EXISTS idx_lead_events_seller_id ON public.lead_events(seller_id);
CREATE INDEX IF NOT EXISTS idx_lead_events_buyer_id ON public.lead_events(buyer_id);
CREATE INDEX IF NOT EXISTS idx_lead_events_status ON public.lead_events(lead_status);
CREATE INDEX IF NOT EXISTS idx_lead_events_created_at ON public.lead_events(created_at DESC);

-- ═══════════════════════════════════════════════════════════════════════════
-- 5. BOOST PLANS & LISTING BOOSTS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.boost_plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    duration_hours  INTEGER NOT NULL CHECK (duration_hours > 0),
    price_amount    NUMERIC(12,2) NOT NULL CHECK (price_amount >= 0),
    score_bonus     INTEGER NOT NULL DEFAULT 0 CHECK (score_bonus >= 0),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.boost_plans ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.listing_boosts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id      UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    seller_id       UUID NOT NULL REFERENCES public.users(id),
    boost_plan_id   UUID NOT NULL REFERENCES public.boost_plans(id),
    payment_status  TEXT NOT NULL DEFAULT 'pending'
                        CHECK (payment_status IN ('pending', 'completed', 'failed', 'refunded')),
    payment_reference TEXT,
    score_bonus     INTEGER NOT NULL DEFAULT 0,
    starts_at       TIMESTAMPTZ NOT NULL,
    ends_at         TIMESTAMPTZ NOT NULL CHECK (ends_at > starts_at),
    active          BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.listing_boosts ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_listing_boosts_listing_id
    ON public.listing_boosts(listing_id);
CREATE INDEX IF NOT EXISTS idx_listing_boosts_seller_id
    ON public.listing_boosts(seller_id);
CREATE INDEX IF NOT EXISTS idx_listing_boosts_active
    ON public.listing_boosts(listing_id, active);
CREATE INDEX IF NOT EXISTS idx_listing_boosts_ends_at
    ON public.listing_boosts(ends_at);

INSERT INTO public.boost_plans (name, duration_hours, price_amount, score_bonus, is_active) VALUES
    ('1-Day Boost', 24, 5000.00, 10, true),
    ('3-Day Boost', 72, 12000.00, 25, true),
    ('Weekly Boost', 168, 25000.00, 50, true),
    ('Top of Category', 168, 50000.00, 100, true)
ON CONFLICT DO NOTHING;

-- ═══════════════════════════════════════════════════════════════════════════
-- 6. REVIEWS & COMMENTS
-- ═══════════════════════════════════════════════════════════════════════════

-- Seller-level reviews (one per buyer-seller pair)
CREATE TABLE IF NOT EXISTS public.seller_reviews (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_id   UUID NOT NULL REFERENCES public.users(id),
    buyer_id    UUID NOT NULL REFERENCES public.users(id),
    rating      INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    comment     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (seller_id, buyer_id)
);
ALTER TABLE public.seller_reviews ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_seller_reviews_seller_id
    ON public.seller_reviews(seller_id);
CREATE INDEX IF NOT EXISTS idx_seller_reviews_rating
    ON public.seller_reviews(seller_id, rating DESC);

-- Product reviews (one per user per product)
CREATE TABLE IF NOT EXISTS public.product_reviews (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id  UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES public.users(id),
    rating      INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    comment     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product_id, user_id)
);
ALTER TABLE public.product_reviews ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_product_reviews_product_id
    ON public.product_reviews(product_id);
CREATE INDEX IF NOT EXISTS idx_product_reviews_user_id
    ON public.product_reviews(user_id);
CREATE INDEX IF NOT EXISTS idx_product_reviews_rating
    ON public.product_reviews(product_id, rating DESC);

-- Product comments (discussion, multiple per user allowed)
CREATE TABLE IF NOT EXISTS public.product_comments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id  UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES public.users(id),
    comment     TEXT NOT NULL,
    is_flagged  BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.product_comments ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_product_comments_product_id
    ON public.product_comments(product_id);

-- Shop comments (discussion)
CREATE TABLE IF NOT EXISTS public.shop_comments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id     UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES public.users(id),
    comment     TEXT NOT NULL,
    is_flagged  BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.shop_comments ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_shop_comments_shop_id
    ON public.shop_comments(shop_id);

-- ═══════════════════════════════════════════════════════════════════════════
-- 7. REPORTS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.product_reports (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id  UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    reporter_id UUID NOT NULL REFERENCES public.users(id),
    reason      TEXT NOT NULL,
    description TEXT,
    resolved    BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.product_reports ENABLE ROW LEVEL SECURITY;

-- ═══════════════════════════════════════════════════════════════════════════
-- 8. FRAUD DETECTION
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.fraud_flags (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_id   UUID REFERENCES public.users(id),
    listing_id  UUID REFERENCES public.products(id),
    event_id    UUID REFERENCES public.listing_events(id),
    flag_type   TEXT NOT NULL CHECK (flag_type IN (
                    'self_click', 'fake_clicks', 'suspicious_traffic',
                    'fake_device', 'abusive_content', 'manipulation'
                )),
    severity    TEXT NOT NULL DEFAULT 'low'
                    CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    resolved    BOOLEAN NOT NULL DEFAULT false,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.fraud_flags ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_fraud_flags_seller_id ON public.fraud_flags(seller_id);
CREATE INDEX IF NOT EXISTS idx_fraud_flags_listing_id ON public.fraud_flags(listing_id);
CREATE INDEX IF NOT EXISTS idx_fraud_flags_resolved ON public.fraud_flags(resolved);
CREATE INDEX IF NOT EXISTS idx_fraud_flags_severity ON public.fraud_flags(severity);
CREATE INDEX IF NOT EXISTS idx_fraud_flags_created_at ON public.fraud_flags(created_at DESC);

-- ═══════════════════════════════════════════════════════════════════════════
-- 9. NOTIFICATIONS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.notifications (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    body        TEXT,
    channel     TEXT NOT NULL DEFAULT 'in-app'
                    CHECK (channel IN ('in-app', 'sms', 'whatsapp', 'email', 'push')),
    status      TEXT NOT NULL DEFAULT 'unread'
                    CHECK (status IN ('unread', 'read', 'sent', 'failed')),
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON public.notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
    ON public.notifications(user_id, status);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at
    ON public.notifications(created_at DESC);

-- ═══════════════════════════════════════════════════════════════════════════
-- 10. CATEGORIES
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.categories (
    slug        TEXT PRIMARY KEY,
    label       TEXT NOT NULL UNIQUE,
    parent_slug TEXT REFERENCES public.categories(slug),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.categories ENABLE ROW LEVEL SECURITY;

-- ═══════════════════════════════════════════════════════════════════════════
-- 11. SEARCH
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.search_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    query       TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.search_history ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_search_history_user_id ON public.search_history(user_id);

-- ═══════════════════════════════════════════════════════════════════════════
-- 12. NATIVE CHAT
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_id        UUID NOT NULL REFERENCES public.users(id),
    seller_id       UUID NOT NULL REFERENCES public.users(id),
    shop_id         UUID REFERENCES public.shops(id) ON DELETE SET NULL,
    product_id      UUID REFERENCES public.products(id) ON DELETE SET NULL,
    last_message    TEXT,
    last_message_at TIMESTAMPTZ,
    buyer_unread    INTEGER NOT NULL DEFAULT 0,
    seller_unread   INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_conversations_buyer_id ON public.conversations(buyer_id);
CREATE INDEX IF NOT EXISTS idx_conversations_seller_id ON public.conversations(seller_id);
CREATE INDEX IF NOT EXISTS idx_conversations_last_msg
    ON public.conversations(last_message_at DESC);

CREATE TABLE IF NOT EXISTS public.messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    sender_id       UUID NOT NULL REFERENCES public.users(id),
    content         TEXT NOT NULL,
    read_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
    ON public.messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at
    ON public.messages(conversation_id, created_at);

-- ═══════════════════════════════════════════════════════════════════════════
-- 13. AI & SHOP CONTEXT
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.shop_ai_context (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id       UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    context_type  TEXT CHECK (context_type IN ('policy', 'faq', 'brand_voice')),
    content       TEXT NOT NULL,
    last_updated  TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.shop_ai_context ENABLE ROW LEVEL SECURITY;

-- ═══════════════════════════════════════════════════════════════════════════
-- 14. SUBSCRIPTIONS & PAYMENTS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.subscriptions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id                 UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    merchant_reference      TEXT UNIQUE NOT NULL,
    pesapal_order_tracking_id TEXT,
    amount                  NUMERIC(12,2) DEFAULT 5000.00,
    currency                TEXT DEFAULT 'UGX',
    payment_status          TEXT DEFAULT 'PENDING',
    payment_method          TEXT,
    ipn_id                  UUID,
    created_at              TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.pesapal_webhook_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payload     JSONB NOT NULL,
    processed   BOOLEAN DEFAULT false,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════════════════
-- 15. ORDERS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id         UUID NOT NULL REFERENCES public.profiles(id),
    shop_id             UUID NOT NULL REFERENCES public.shops(id),
    total_amount        NUMERIC(12,2) NOT NULL,
    order_status        TEXT DEFAULT 'pending'
                            CHECK (order_status IN ('pending', 'processing', 'shipped', 'delivered', 'cancelled')),
    pesapal_tracking_id TEXT,
    created_at          TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;

-- ═══════════════════════════════════════════════════════════════════════════
-- HELPER FUNCTIONS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.increment_shop_view_count(p_shop_id uuid)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE v bigint;
BEGIN
    UPDATE public.shops
    SET view_count = view_count + 1, updated_at = now()
    WHERE id = p_shop_id
    RETURNING view_count INTO v;
    RETURN v;
END;
$$;

CREATE OR REPLACE FUNCTION public.increment_product_view_count(p_product_id uuid)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE v bigint;
BEGIN
    UPDATE public.products
    SET view_count = view_count + 1
    WHERE id = p_product_id
    RETURNING view_count INTO v;
    RETURN v;
END;
$$;

CREATE OR REPLACE FUNCTION public.increment_unread(p_field text, p_conversation_id uuid)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE v_new INTEGER;
BEGIN
    IF p_field = 'buyer_unread' THEN
        UPDATE public.conversations
        SET buyer_unread = buyer_unread + 1
        WHERE id = p_conversation_id
        RETURNING buyer_unread INTO v_new;
    ELSE
        UPDATE public.conversations
        SET seller_unread = seller_unread + 1
        WHERE id = p_conversation_id
        RETURNING seller_unread INTO v_new;
    END IF;
    RETURN COALESCE(v_new, 0);
END;
$$;

GRANT EXECUTE ON FUNCTION public.increment_shop_view_count(uuid) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.increment_product_view_count(uuid) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.increment_unread(text, uuid) TO service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- RLS POLICIES (idempotent — safe to re-run)
-- ═══════════════════════════════════════════════════════════════════════════

DROP POLICY IF EXISTS "shops_select_public" ON public.shops;
CREATE POLICY "shops_select_public" ON public.shops FOR SELECT USING (true);

DROP POLICY IF EXISTS "shops_all_owner" ON public.shops;
CREATE POLICY "shops_all_owner" ON public.shops FOR ALL USING (owner_id = auth.uid());

DROP POLICY IF EXISTS "profiles_own" ON public.profiles;
CREATE POLICY "profiles_own" ON public.profiles FOR ALL USING (id = auth.uid());

DROP POLICY IF EXISTS "users_own" ON public.users;
CREATE POLICY "users_own" ON public.users FOR ALL USING (id = auth.uid());
