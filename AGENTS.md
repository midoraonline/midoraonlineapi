# AGENTS.md — Midora API

Authoritative backend rules for `midoraapi`.

**Read the frontend sibling doc for product-wide standards:**
`../midora/AGENTS.md` (especially §1, §5, §6).

## Stack

FastAPI · Pydantic Settings · Supabase/PostgREST · PyJWT + bcrypt · Pesapal · Vercel Python

## Must-follow

1. Feature modules under top-level packages; register via `app/factory/routers.py`.
2. Enforce shop/product ownership with `core/authz.py` on mutations.
3. Do not weaken production Pesapal verification or JWT secret boot checks.
4. Prefer restoring user-scoped Supabase/RLS over permanent service-role defaults.
5. Keep error envelopes stable (`detail` + `code`).
6. Conventional commits; add pytest for auth, authz, and payment webhook changes.
