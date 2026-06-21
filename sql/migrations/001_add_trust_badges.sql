-- Migration: Add trust_badges to shops table
-- Run this in the Supabase SQL Editor

ALTER TABLE public.shops 
ADD COLUMN IF NOT EXISTS trust_badges TEXT[] DEFAULT ARRAY['shop_listed']::TEXT[];

-- Update existing shops to have the default badge if they don't already
UPDATE public.shops 
SET trust_badges = ARRAY['shop_listed']::TEXT[] 
WHERE trust_badges IS NULL;
