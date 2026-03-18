# Shops & AI Concierge — Routes and Usage

Base URL (local): `http://127.0.0.1:8000`. All routes below are under `/api/v1`.

---

## 1. Creating a shop

### Route

- **`POST /api/v1/shops/`**
- **Auth:** Required. Send `Authorization: Bearer <access_token>` (from `POST /api/v1/auth/login`).

### Request body (JSON)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | **Yes** | Shop display name |
| `slug` | string | **Yes** | URL-friendly identifier (lowercase, hyphens, no spaces). Must be unique. |
| `description` | string | No | Short description of the shop |
| `about` | string | No | Longer “about” text |
| `logo_url` | string | No | URL of the shop logo image |
| `shop_email` | string | No | Contact email for the shop |
| `whatsapp_number` | string | No | WhatsApp contact number |
| `contacts` | array of objects | No | Additional contact entries (e.g. `[{ "label": "Phone", "value": "+123..." }]`) |
| `social_links` | array of objects | No | Social links (e.g. `[{ "platform": "instagram", "url": "https://..." }]`) |
| `location` | object | No | Location info (e.g. address, city, country) |
| `availability` | object | No | Opening hours / availability |
| `theme_config` | object | No | UI/theme preferences |
| `shop_type` | string | No | One of `"product"`, `"service"`, `"both"`. Default: `"product"`. |

### Example

```json
{
  "name": "My Coffee Shop",
  "slug": "my-coffee-shop",
  "description": "Fresh coffee and pastries",
  "shop_type": "both",
  "shop_email": "hello@mycoffee.com",
  "whatsapp_number": "+256700000000"
}
```

### Response

Returns the created shop (same shape as `ShopResponse`): `id`, `owner_id`, `name`, `slug`, `description`, `about`, `logo_url`, `shop_email`, `whatsapp_number`, `contacts`, `social_links`, `location`, `availability`, `theme_config`, `shop_type`, `is_active`, `subscription_end_date`, `created_at`, `updated_at`.

### From the frontend (e.g. Next.js)

```ts
const token = "…"; // from login
const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/v1/shops/`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  },
  body: JSON.stringify({
    name: "My Coffee Shop",
    slug: "my-coffee-shop",
    description: "Fresh coffee and pastries",
    shop_type: "both",
  }),
});
const shop = await res.json();
```

---

## 2. AI Concierge — Overview

There are **three** chat-related flows:

| Flow | Purpose | Route(s) |
|------|---------|----------|
| **Midora info bot** | Answer questions about Midora Online only (no shop, no session) | `POST /api/v1/chat/midora` |
| **In-shop concierge** | Chat about a specific shop (products, policies, etc.) | Create session with `shop_id`, then `POST .../messages` |
| **Create-shop concierge** | Guided conversation to collect name, type, description and get a suggested shop payload | Create session with `intent: "create_shop"`, then `POST .../messages`; use `suggested_shop` → `POST /api/v1/shops/` |

---

## 3. Midora info bot (no session)

- **Route:** `POST /api/v1/chat/midora`
- **Auth:** None.
- **Request:** `{ "message": "What is Midora?" }`
- **Response:** `{ "message": "<AI reply about Midora Online>" }`

Use this for a simple “Ask about Midora” widget. No sessions or shop context.

```ts
const res = await fetch(`${API_BASE}/api/v1/chat/midora`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "How do I create a shop?" }),
});
const { message } = await res.json();
```

---

## 4. In-shop concierge (session + messages)

The bot uses **shop AI context** (and optionally product info) to answer questions about that shop.

### Step 1: Create a chat session for the shop

- **Route:** `POST /api/v1/chat/sessions`
- **Auth:** Optional (Bearer for logged-in customer).
- **Body:** `{ "shop_id": "<shop_uuid>" }`  
  Do **not** set `intent` for in-shop chat.

**Response:** Session object with `id` (and other fields). Save `id` for the next step.

### Step 2: Send messages and get replies

- **Route:** `POST /api/v1/chat/sessions/{session_id}/messages`
- **Auth:** Optional (same as above).
- **Body:** `{ "message": "Do you ship internationally?" }`

**Response:**  
`{ "message": "<concierge reply>", "sender_type": "ai_concierge", "suggested_shop": null }`

### Step 3: List messages (optional)

- **Route:** `GET /api/v1/chat/sessions/{session_id}/messages`
- **Response:** Array of messages (customer and `ai_concierge`) in order.

### Shop AI context (what the concierge reads)

- **List:** `GET /api/v1/shops/{shop_id}/ai-context` (Bearer, shop owner).
- **Add:** `POST /api/v1/shops/{shop_id}/ai-context` (Bearer) with body `{ "context_type": "policy", "content": "We ship worldwide." }`.

The in-shop concierge uses these context entries to answer questions; add policies, FAQs, and product highlights here.

---

## 5. Create-shop concierge (guided shop creation)

The AI asks for business name, type (product / service / both), and a short description, then returns a **suggested shop** payload you can send to `POST /api/v1/shops/`.

### Step 1: Create a “create shop” session

- **Route:** `POST /api/v1/chat/sessions`
- **Body:** `{ "intent": "create_shop" }`  
  Do **not** send `shop_id` (or leave it null).

**Response:** Session object with `id`. Use this for all steps below.

### Step 2: Chat with the concierge

- **Route:** `POST /api/v1/chat/sessions/{session_id}/messages`
- **Body:** `{ "message": "I want to open a bakery" }` (or any reply to the AI’s questions).

**Response:**  
`{ "message": "<AI reply>", "sender_type": "ai_concierge", "suggested_shop": null | { ... } }`

- Until the AI has enough info (name, type, description), `suggested_shop` is `null`.
- When the AI has enough, it returns a **markdown JSON block** in `message` and may also populate **`suggested_shop`** with:  
  `{ "name", "slug", "description", "logo_url"?, "shop_type" }`.

### Step 3: Create the shop from the suggestion

When `suggested_shop` is non-null, you can create the shop in one of two ways:

- **Option A — Use the object as-is (fill only required fields):**  
  Send `POST /api/v1/shops/` (with Bearer) and body:
  ```json
  {
    "name": "<suggested_shop.name>",
    "slug": "<suggested_shop.slug>",
    "description": "<suggested_shop.description or null>",
    "shop_type": "<suggested_shop.shop_type>"
  }
  ```
- **Option B — Let the user edit:**  
  Pre-fill your “Create shop” form with `suggested_shop` and then submit to `POST /api/v1/shops/` when the user confirms.

### Example flow (create-shop)

```ts
// 1) Create session
const sessionRes = await fetch(`${API_BASE}/api/v1/chat/sessions`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  body: JSON.stringify({ intent: "create_shop" }),
});
const { id: sessionId } = await sessionRes.json();

// 2) Send messages
const msgRes = await fetch(`${API_BASE}/api/v1/chat/sessions/${sessionId}/messages`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "I want to open a small bakery selling bread and cakes." }),
});
const { message, suggested_shop } = await msgRes.json();
// Show `message` in the chat UI. If `suggested_shop` is set, show “Create shop” with pre-filled form.

// 3) When user confirms, create shop
if (suggested_shop) {
  await fetch(`${API_BASE}/api/v1/shops/`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      name: suggested_shop.name,
      slug: suggested_shop.slug,
      description: suggested_shop.description ?? undefined,
      shop_type: suggested_shop.shop_type,
    }),
  });
}
```

---

## 6. Quick reference — Chat routes

| Route | Method | Auth | Body / Notes |
|-------|--------|------|--------------|
| Midora info bot | `POST /api/v1/chat/midora` | No | `{ "message": "..." }` |
| Create chat session | `POST /api/v1/chat/sessions` | Optional | `{ "shop_id": "..." }` or `{ "intent": "create_shop" }` |
| List my sessions | `GET /api/v1/chat/sessions` | Optional | Query: `?shop_id=...` |
| Send message | `POST /api/v1/chat/sessions/{id}/messages` | Optional | `{ "message": "..." }` → returns `message`, `suggested_shop` |
| Get messages | `GET /api/v1/chat/sessions/{id}/messages` | Optional | List messages for session |

---

## 7. Quick reference — Create shop payload

**Required:** `name`, `slug`.  
**Optional:** `description`, `about`, `logo_url`, `shop_email`, `whatsapp_number`, `contacts`, `social_links`, `location`, `availability`, `theme_config`, `shop_type` (default `"product"`).

Use **`POST /api/v1/shops/`** with **Bearer** token; response is the created shop object.
