-- Phase 12: Native person-to-person chat

-- Conversations track 1:1 buyer ↔ seller threads
CREATE TABLE IF NOT EXISTS public.conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_id UUID NOT NULL REFERENCES public.users(id),
    seller_id UUID NOT NULL REFERENCES public.users(id),
    shop_id UUID REFERENCES public.shops(id) ON DELETE SET NULL,
    product_id UUID REFERENCES public.products(id) ON DELETE SET NULL,
    last_message TEXT,
    last_message_at TIMESTAMPTZ,
    buyer_unread INTEGER NOT NULL DEFAULT 0,
    seller_unread INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_conversations_buyer_id ON public.conversations(buyer_id);
CREATE INDEX IF NOT EXISTS idx_conversations_seller_id ON public.conversations(seller_id);
CREATE INDEX IF NOT EXISTS idx_conversations_shop_id ON public.conversations(shop_id);
CREATE INDEX IF NOT EXISTS idx_conversations_last_msg ON public.conversations(last_message_at DESC);

-- Messages within conversations
CREATE TABLE IF NOT EXISTS public.messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    sender_id UUID NOT NULL REFERENCES public.users(id),
    content TEXT NOT NULL,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON public.messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON public.messages(conversation_id, created_at);

-- Function to get or create conversation
CREATE OR REPLACE FUNCTION public.get_or_create_conversation(
    p_buyer_id uuid,
    p_seller_id uuid,
    p_shop_id uuid DEFAULT NULL,
    p_product_id uuid DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_conversation public.conversations;
    v_result jsonb;
BEGIN
    SELECT * INTO v_conversation
    FROM public.conversations
    WHERE buyer_id = p_buyer_id AND seller_id = p_seller_id
    ORDER BY updated_at DESC
    LIMIT 1;

    IF v_conversation.id IS NULL THEN
        INSERT INTO public.conversations (buyer_id, seller_id, shop_id, product_id)
        VALUES (p_buyer_id, p_seller_id, p_shop_id, p_product_id)
        RETURNING * INTO v_conversation;
    END IF;

    SELECT row_to_json(v_conversation) INTO v_result;
    RETURN v_result;
END;
$$;

GRANT EXECUTE ON FUNCTION public.get_or_create_conversation(uuid, uuid, uuid, uuid) TO service_role;

-- Increment helper for atomic counter updates
CREATE OR REPLACE FUNCTION public.increment_unread(p_field text, p_conversation_id uuid)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_new INTEGER;
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

GRANT EXECUTE ON FUNCTION public.increment_unread(text, uuid) TO service_role;
