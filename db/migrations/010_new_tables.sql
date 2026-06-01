-- Phase 2: New tables for Midora marketplace features

-- 2a. listing_images: separate image storage with sort order
CREATE TABLE IF NOT EXISTS public.listing_images (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    image_url TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.listing_images ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_listing_images_listing_id ON public.listing_images(listing_id);
CREATE INDEX IF NOT EXISTS idx_listing_images_sort ON public.listing_images(listing_id, sort_order);

-- 2b. listing_events: activity tracking for all listing interactions
CREATE TABLE IF NOT EXISTS public.listing_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID REFERENCES public.products(id) ON DELETE CASCADE,
    seller_id UUID REFERENCES public.users(id),
    buyer_id UUID REFERENCES public.users(id),
    session_id TEXT,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'viewed', 'whatsapp_clicked', 'call_clicked', 'saved',
        'shared', 'reported', 'updated'
    )),
    ip_address TEXT,
    device_hash TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.listing_events ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_listing_events_listing_id ON public.listing_events(listing_id);
CREATE INDEX IF NOT EXISTS idx_listing_events_seller_id ON public.listing_events(seller_id);
CREATE INDEX IF NOT EXISTS idx_listing_events_buyer_id ON public.listing_events(buyer_id);
CREATE INDEX IF NOT EXISTS idx_listing_events_event_type ON public.listing_events(event_type);
CREATE INDEX IF NOT EXISTS idx_listing_events_created_at ON public.listing_events(created_at DESC);

-- 2c. lead_events: serious lead/contact tracking for sellers
CREATE TABLE IF NOT EXISTS public.lead_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    seller_id UUID NOT NULL REFERENCES public.users(id),
    buyer_id UUID REFERENCES public.users(id),
    source TEXT NOT NULL CHECK (source IN ('whatsapp', 'call', 'contact_form', 'email')),
    lead_status TEXT NOT NULL DEFAULT 'new' CHECK (lead_status IN ('new', 'responded', 'ignored', 'closed')),
    unique_key TEXT UNIQUE NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.lead_events ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_lead_events_listing_id ON public.lead_events(listing_id);
CREATE INDEX IF NOT EXISTS idx_lead_events_seller_id ON public.lead_events(seller_id);
CREATE INDEX IF NOT EXISTS idx_lead_events_buyer_id ON public.lead_events(buyer_id);
CREATE INDEX IF NOT EXISTS idx_lead_events_status ON public.lead_events(lead_status);
CREATE INDEX IF NOT EXISTS idx_lead_events_created_at ON public.lead_events(created_at DESC);

-- 2d. boost_plans: paid visibility packages
CREATE TABLE IF NOT EXISTS public.boost_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    duration_hours INTEGER NOT NULL CHECK (duration_hours > 0),
    price_amount NUMERIC(12, 2) NOT NULL CHECK (price_amount >= 0),
    score_bonus INTEGER NOT NULL DEFAULT 0 CHECK (score_bonus >= 0),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.boost_plans ENABLE ROW LEVEL SECURITY;

-- 2e. listing_boosts: boost purchases applied to listings
CREATE TABLE IF NOT EXISTS public.listing_boosts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    seller_id UUID NOT NULL REFERENCES public.users(id),
    boost_plan_id UUID NOT NULL REFERENCES public.boost_plans(id),
    payment_status TEXT NOT NULL DEFAULT 'pending' CHECK (payment_status IN ('pending', 'completed', 'failed', 'refunded')),
    payment_reference TEXT,
    score_bonus INTEGER NOT NULL DEFAULT 0,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL CHECK (ends_at > starts_at),
    active BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.listing_boosts ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_listing_boosts_listing_id ON public.listing_boosts(listing_id);
CREATE INDEX IF NOT EXISTS idx_listing_boosts_seller_id ON public.listing_boosts(seller_id);
CREATE INDEX IF NOT EXISTS idx_listing_boosts_active ON public.listing_boosts(listing_id, active);
CREATE INDEX IF NOT EXISTS idx_listing_boosts_ends_at ON public.listing_boosts(ends_at);

-- 2f. seller_reviews: buyer feedback on sellers
CREATE TABLE IF NOT EXISTS public.seller_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_id UUID NOT NULL REFERENCES public.users(id),
    buyer_id UUID NOT NULL REFERENCES public.users(id),
    rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (seller_id, buyer_id)
);
ALTER TABLE public.seller_reviews ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_seller_reviews_seller_id ON public.seller_reviews(seller_id);
CREATE INDEX IF NOT EXISTS idx_seller_reviews_rating ON public.seller_reviews(seller_id, rating DESC);

-- 2g. fraud_flags: suspicious activity records
CREATE TABLE IF NOT EXISTS public.fraud_flags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_id UUID REFERENCES public.users(id),
    listing_id UUID REFERENCES public.products(id),
    event_id UUID REFERENCES public.listing_events(id),
    flag_type TEXT NOT NULL CHECK (flag_type IN (
        'self_click', 'fake_clicks', 'suspicious_traffic',
        'fake_device', 'abusive_content', 'manipulation'
    )),
    severity TEXT NOT NULL DEFAULT 'low' CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    resolved BOOLEAN NOT NULL DEFAULT false,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.fraud_flags ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_fraud_flags_seller_id ON public.fraud_flags(seller_id);
CREATE INDEX IF NOT EXISTS idx_fraud_flags_listing_id ON public.fraud_flags(listing_id);
CREATE INDEX IF NOT EXISTS idx_fraud_flags_resolved ON public.fraud_flags(resolved);
CREATE INDEX IF NOT EXISTS idx_fraud_flags_severity ON public.fraud_flags(severity);
CREATE INDEX IF NOT EXISTS idx_fraud_flags_created_at ON public.fraud_flags(created_at DESC);

-- 2h. notifications: user-facing messages
CREATE TABLE IF NOT EXISTS public.notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT,
    channel TEXT NOT NULL DEFAULT 'in-app' CHECK (channel IN ('in-app', 'sms', 'whatsapp', 'email', 'push')),
    status TEXT NOT NULL DEFAULT 'unread' CHECK (status IN ('unread', 'read', 'sent', 'failed')),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON public.notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread ON public.notifications(user_id, status);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON public.notifications(created_at DESC);

-- Seed initial boost plans
INSERT INTO public.boost_plans (name, duration_hours, price_amount, score_bonus, is_active) VALUES
    ('1-Day Boost', 24, 5000.00, 10, true),
    ('3-Day Boost', 72, 12000.00, 25, true),
    ('Weekly Boost', 168, 25000.00, 50, true),
    ('Top of Category', 168, 50000.00, 100, true)
ON CONFLICT DO NOTHING;
