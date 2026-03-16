-- DigitalMall schema for Supabase (run in SQL Editor)
-- 1. Shops (tenant entity)
CREATE TABLE IF NOT EXISTS public.shops (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES auth.users(id),
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    description TEXT,
    logo_url TEXT,
    theme_config JSONB DEFAULT '{"primary_color": "#000000", "font": "Inter"}'::jsonb,
    shop_type TEXT NOT NULL DEFAULT 'product' CHECK (shop_type IN ('product', 'service', 'both')),
    is_active BOOLEAN DEFAULT false,
    subscription_end_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.shops ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_shops_owner_id ON public.shops(owner_id);
CREATE INDEX IF NOT EXISTS idx_shops_slug ON public.shops(slug);

-- 2. Profiles (extends auth.users)
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    full_name TEXT,
    avatar_url TEXT,
    phone_number TEXT UNIQUE,
    user_role TEXT CHECK (user_role IN ('merchant', 'customer', 'admin')) DEFAULT 'customer',
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- 3. Products
CREATE TABLE IF NOT EXISTS public.products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    price_ugx NUMERIC(12, 2) NOT NULL,
    stock_quantity INTEGER DEFAULT 0,
    image_urls TEXT,
    category TEXT,
    ai_seo_tags TEXT,
    ai_generated_desc BOOLEAN DEFAULT false,
    is_published BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_products_shop_id ON public.products(shop_id);
CREATE INDEX IF NOT EXISTS idx_products_created_at ON public.products(created_at DESC);
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;

-- 4. Orders
CREATE TABLE IF NOT EXISTS public.orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES public.profiles(id),
    shop_id UUID NOT NULL REFERENCES public.shops(id),
    total_amount NUMERIC(12, 2) NOT NULL,
    order_status TEXT DEFAULT 'pending' CHECK (order_status IN ('pending', 'processing', 'shipped', 'delivered', 'cancelled')),
    pesapal_tracking_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;

-- 5. Shop AI context
CREATE TABLE IF NOT EXISTS public.shop_ai_context (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    context_type TEXT CHECK (context_type IN ('policy', 'faq', 'brand_voice')),
    content TEXT NOT NULL,
    last_updated TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.shop_ai_context ENABLE ROW LEVEL SECURITY;

-- 6. Chat (shop_id nullable when intent = 'create_shop')
CREATE TABLE IF NOT EXISTS public.chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES public.profiles(id),
    shop_id UUID REFERENCES public.shops(id),
    intent TEXT CHECK (intent IN ('create_shop')),
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES public.chat_sessions(id) ON DELETE CASCADE,
    sender_type TEXT CHECK (sender_type IN ('customer', 'ai_concierge', 'merchant')),
    message TEXT NOT NULL,
    thought_signature TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_messages ENABLE ROW LEVEL SECURITY;

-- 7. Subscriptions
CREATE TABLE IF NOT EXISTS public.subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    merchant_reference TEXT UNIQUE NOT NULL,
    pesapal_order_tracking_id TEXT,
    amount NUMERIC(12, 2) DEFAULT 5000.00,
    currency TEXT DEFAULT 'UGX',
    payment_status TEXT DEFAULT 'PENDING',
    payment_method TEXT,
    ipn_id UUID,
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;

-- 8. Pesapal webhook logs
CREATE TABLE IF NOT EXISTS public.pesapal_webhook_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payload JSONB NOT NULL,
    processed BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS policies (examples; adjust to your rules)
-- Shops: owner can all, public can select
CREATE POLICY "shops_select_public" ON public.shops FOR SELECT USING (true);
CREATE POLICY "shops_all_owner" ON public.shops FOR ALL USING (owner_id = auth.uid());

-- Profiles: own profile only
CREATE POLICY "profiles_own" ON public.profiles FOR ALL USING (id = auth.uid());

-- Migrations for existing DBs (run if tables already exist):
-- ALTER TABLE public.shops ADD COLUMN IF NOT EXISTS shop_type TEXT NOT NULL DEFAULT 'product';
-- ALTER TABLE public.chat_sessions ALTER COLUMN shop_id DROP NOT NULL;
-- ALTER TABLE public.chat_sessions ADD COLUMN IF NOT EXISTS intent TEXT;
