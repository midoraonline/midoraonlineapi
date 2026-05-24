# DigitalMall API (Midora) — Routes & Next.js Usage

## Base URL

- Local dev: `http://127.0.0.1:8000`
- All API routes are under: `/api/v1`

## Built-in API docs (FastAPI)

- Swagger UI: `GET /docs`
- OpenAPI JSON: `GET /openapi.json`
- Redoc: `GET /redoc`

## Auth overview

- Most protected routes require: `Authorization: Bearer <access_token>`
- Admin routes require: `X-Admin-Key: <ADMIN_API_KEY>` (if `ADMIN_API_KEY` is unset, admin access is allowed in dev)
 - Access and refresh tokens are custom JWTs issued by this API (not Supabase Auth), signed with `APP_JWT_SECRET` and including `sub` (user id) and `role`.

## Routes

### Health

- `GET /api/v1/health`
  - Purpose: Uptime check (status + service/version + UTC time)

### Auth (`/api/v1/auth/*`)

- `POST /api/v1/auth/register`
  - Purpose: Create an account and return JWT access/refresh tokens
- `POST /api/v1/auth/login`
  - Purpose: Sign in; returns `access_token` and `refresh_token`
- `POST /api/v1/auth/refresh`
  - Purpose: Refresh session using `refresh_token`
- `GET /api/v1/auth/me` (Bearer)
  - Purpose: Get current user profile
- `POST /api/v1/auth/verify-email`
  - Purpose: Currently not implemented for custom auth (returns 400)

### Categories (`/api/v1/categories/*`)

- `GET /api/v1/categories/`
  - Purpose: List canonical browse categories (seeded in `public.categories`)
  - Response: `{ "items": [{ "slug", "label", "sort_order" }, ...] }`
  - Shop and product `category` fields must use one of these **labels** (validated on create/update)

### Shops / tenants (`/api/v1/shops/*`)

- `GET /api/v1/shops/`
  - Purpose: Public shop discovery list (paginated; optional `search`, `shop_type`)
- `GET /api/v1/shops/by-slug/{slug}`
  - Purpose: Public shop lookup by slug
- `GET /api/v1/shops/me` (Bearer)
  - Purpose: List shops owned by the current user (paginated)
- `POST /api/v1/shops/` (Bearer)
  - Purpose: Create a shop
  - Shop details supported (selected): `about`, `shop_email`, `whatsapp_number`, `contacts[]`, `social_links[]`, `location`, `availability`, plus `shop_type`
- `GET /api/v1/shops/{shop_id}`
  - Purpose: Get a shop by id
- `PATCH /api/v1/shops/{shop_id}` (Bearer)
  - Purpose: Update a shop
  - Supports updating the same extended shop details as create
- `POST /api/v1/shops/{shop_id}/logo/generate` (Bearer)
  - Purpose: Placeholder for AI logo generation (currently returns an empty `logo_url`)

### Products

- `POST /api/v1/shops/{shop_id}/products` (Bearer)
  - Purpose: Create product for a shop
- `GET /api/v1/shops/{shop_id}/products`
  - Purpose: List products in a shop (paginated; optional `category`, `search`; optional Bearer)
- `GET /api/v1/{product_id}`
  - Purpose: Get product by id
- `PATCH /api/v1/{product_id}` (Bearer)
  - Purpose: Update product by id
- `DELETE /api/v1/{product_id}` (Bearer)
  - Purpose: Delete product by id
- `POST /api/v1/generate-from-image` (Bearer)
  - Purpose: Placeholder “generate product details from image” (currently returns empty fields)

### Orders

> Note: these are currently mounted at the API root (`/api/v1/`) due to router configuration.

- `POST /api/v1/` (Bearer)
  - Purpose: Create order
- `GET /api/v1/` (Bearer)
  - Purpose: List orders for the current user (paginated; RLS-based)
- `PATCH /api/v1/{order_id}` (Bearer)
  - Purpose: Update an order status

### AI context (`/api/v1/shops/*`)

- `GET /api/v1/shops/{shop_id}/ai-context` (Bearer)
  - Purpose: List AI context entries for a shop
- `POST /api/v1/shops/{shop_id}/ai-context` (Bearer)
  - Purpose: Create an AI context entry for a shop

### Chat (`/api/v1/chat/*`)

- `POST /api/v1/chat/sessions`
  - Purpose: Create chat session (optional Bearer; requires `shop_id` unless `intent="create_shop"`)
- `GET /api/v1/chat/sessions`
  - Purpose: List chat sessions (optional filters: `shop_id`; optional Bearer)
- `POST /api/v1/chat/sessions/{session_id}/messages`
  - Purpose: Send a message; stores conversation and returns AI reply
- `GET /api/v1/chat/sessions/{session_id}/messages`
  - Purpose: List messages for a session
 - `POST /api/v1/chat/midora`
   - Purpose: Simple Midora Online info bot (no sessions); send `{ "message": "..." }` and receive `{ "message": "..." }`

### AI images (`/api/v1/ai/*`)

- `POST /api/v1/ai/remove-background` (Bearer)
  - Purpose: Remove image background; returns processed image URL

### Payments (`/api/v1/payments/*`)

- `POST /api/v1/payments/subscribe` (Bearer)
  - Purpose: Create subscription intent (e.g. with `shop_id`, `amount`, `currency`)
- `GET /api/v1/payments/subscriptions` (Bearer)
  - Purpose: List subscriptions for current user
- `POST /api/v1/payments/webhook`
  - Purpose: Public webhook receiver; responds `{"received": true}`

### Admin (`/api/v1/admin/*`) — requires `X-Admin-Key` (unless unset in dev)

- `GET /api/v1/admin/shops/`
  - Purpose: List all shops (paginated)
- `PATCH /api/v1/admin/shops/{shop_id}/active`
  - Purpose: Set shop active/inactive (query param `is_active`, default `true`)
- `GET /api/v1/admin/subscriptions/`
  - Purpose: List all subscriptions

## Connecting from a Next.js app

### 1) Set base URL

Create/update `NEXT_PUBLIC_API_BASE_URL` in `.env.local`:

```bash
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

### 2) Minimal fetch helper

```ts
// lib/api.ts
const BASE = process.env.NEXT_PUBLIC_API_BASE_URL!;

export async function apiFetch<T>(
  path: string,
  opts: RequestInit & { token?: string } = {}
): Promise<T> {
  const { token, headers, ...rest } = opts;

  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(headers || {}),
    },
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text || res.statusText}`);
  }

  return (await res.json()) as T;
}
```

### 3) Examples

Health check:

```ts
await apiFetch("/api/v1/health");
```

Login:

```ts
await apiFetch("/api/v1/auth/login", {
  method: "POST",
  body: JSON.stringify({ email, password }),
});
```

Authenticated request (`/auth/me`):

```ts
await apiFetch("/api/v1/auth/me", { token: accessToken });
```

Admin request:

```ts
await apiFetch("/api/v1/admin/shops/", {
  headers: { "X-Admin-Key": adminKey },
});
```

