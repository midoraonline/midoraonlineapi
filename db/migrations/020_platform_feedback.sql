CREATE TABLE IF NOT EXISTS public.platform_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id),
    feedback_text TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Enable RLS
ALTER TABLE public.platform_feedback ENABLE ROW LEVEL SECURITY;

-- Allow authenticated and anonymous users to insert feedback
CREATE POLICY "Anyone can insert feedback" ON public.platform_feedback FOR INSERT WITH CHECK (true);

-- Allow admins to view all feedback
CREATE POLICY "Admins can view feedback" ON public.platform_feedback FOR SELECT USING (
    (auth.uid() IN ( SELECT users.id FROM users WHERE users.user_role = 'admin'::text))
);
