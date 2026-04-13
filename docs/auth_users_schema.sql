-- Custom users table for application-managed auth (separate from auth.users)
create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  full_name text,
  user_role text not null default 'customer' check (user_role in ('merchant', 'customer', 'admin', 'staff')),
  email_verified boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.users is 'Application-managed users for custom auth (not Supabase auth.users).';

-- Example RLS policy alignment (to be applied manually in Supabase):
-- alter table public.users enable row level security;
-- create policy \"Users can view/update own row\" on public.users
--   for all using (id = auth.uid());


