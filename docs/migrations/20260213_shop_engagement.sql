-- Shop follows, likes, product likes, and extended user roles.
-- Run in Supabase SQL Editor on existing projects.

-- ---------------------------------------------------------------------------
-- User roles: allow staff (and keep existing values)
-- ---------------------------------------------------------------------------
ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_user_role_check;
ALTER TABLE public.users ADD CONSTRAINT users_user_role_check
  CHECK (user_role IN ('merchant', 'customer', 'admin', 'staff'));

ALTER TABLE public.profiles DROP CONSTRAINT IF EXISTS profiles_user_role_check;
ALTER TABLE public.profiles ADD CONSTRAINT profiles_user_role_check
  CHECK (user_role IN ('merchant', 'customer', 'admin', 'staff'));

-- ---------------------------------------------------------------------------
-- Engagement tables (API uses service role; RLS on for defense in depth)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.shop_follows (
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    shop_id UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, shop_id)
);
CREATE INDEX IF NOT EXISTS idx_shop_follows_shop_id ON public.shop_follows(shop_id);

CREATE TABLE IF NOT EXISTS public.shop_likes (
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    shop_id UUID NOT NULL REFERENCES public.shops(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, shop_id)
);
CREATE INDEX IF NOT EXISTS idx_shop_likes_shop_id ON public.shop_likes(shop_id);

CREATE TABLE IF NOT EXISTS public.product_likes (
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_product_likes_product_id ON public.product_likes(product_id);

ALTER TABLE public.shop_follows ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.shop_likes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_likes ENABLE ROW LEVEL SECURITY;
