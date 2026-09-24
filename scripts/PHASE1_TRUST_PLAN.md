# Midora Phase 1 Trust Plan (locked)

**Status:** Locked with Joel — 22 Sep 2026 (EAT)  
**Scope:** Trust, listing quality, and marketplace hygiene for MVP growth  
**Principle:** Jiji / Facebook Marketplace style trust — visible seller signals, real phone, real place, real photos, report/block. Not KYC-first.  
**Constraint:** Keep algorithms and product rules simple and proven. Ship in weeks, not months.

This plan comes from the Midora trust / spam / chicken-and-egg notes, audited against the current `midora` + `midoraapi` codebases. Phase 1 from the original checklist was tightened because it mixed hygiene, anti-spam ML, reputation, and KYC into one phase.

---

## Goals for Phase 1

Buyers should silently get answers to:

1. Is this real?
2. Can I trust this person?
3. Will they respond?

Sellers should get a fast path to a useful listing and replies, without Midora feeling emptier or less trustworthy than WhatsApp groups / Facebook / Jiji.

---

## What Midora already has (do not rebuild)

Use and tighten these; do not rewrite:

| Area | Already in code |
|------|-----------------|
| Phone OTP | Africa’s Talking OTP (`auth/routes/phone_verify.py`, verification service, migration `036`) |
| Unique phone columns | `users.phone_number` / profiles uniqueness |
| Presence | `last_seen_at` + “Live now” (`marketplace/presence_service.py`) |
| Report listing + admin | Reports API + `ReportListing` + admin resolve |
| Listing moderation | Keywords → pHash blocklist → metadata → Gemini text/image |
| Description quality | ≥40 chars, ≥2 sentences; AI category suggestion |
| Original-photo push | EXIF/stock gates + watermark |
| WhatsApp contact + leads | Product/shop CTAs + lead recording |
| Feed cold-start / boosts | Feed scoring + boost service |
| Later-ready | Reviews, identity/business verification stages, backend trust fields |

---

## Phase 1 — Must (1–3 weeks)

Ship these. This is the locked Must list.

### 1. Phone trust before commerce

- [ ] Require **phone verified** before **publish listing** and before showing / using **WhatsApp CTA**
- [ ] Enforce **one account per phone** with clear UX when the number is already claimed (DB unique already exists; finish the product path)
- [ ] No silent multi-account side doors on verify/update

### 2. Harder listing basics (publish rules)

- [ ] **Location required** on shop and/or listing before publish (no silent “Uganda” fallback for published inventory)
- [ ] **Price required** for product listings (`price > 0`); allow quote-style only where the product kind intentionally supports it
- [ ] **≥ 2 photos** required to publish
- [ ] Raise photo **max to 6–8** (today: min 1, max 3 — inverted vs the trust notes)
- [ ] Keep existing description minimum and category required + AI suggestion

### 3. Buyer-visible seller signals (data already exists)

On **product detail** and **shop storefront**, show:

- [ ] **Joined** (shop / seller `created_at` → “Member since …”)
- [ ] **Last active** (relative from `last_seen_at`) plus keep **Live now** when online
- [ ] Do **not** invent response rate / completed deals in Must until volume exists (those are Phase 2)

### 4. Safety controls buyers expect

- [ ] **Report listing** — keep current flow
- [ ] **Report seller** — thin product path (entity + reason), admin can act
- [ ] **Block seller** — buyer-side block list (hide seller / stop contact); admin `blocked` remains ops control

### 5. Keep moderation pipeline on

- [ ] Do not disable keyword / metadata / Gemini pipeline while adding Must items
- [ ] No full reverse-image search or enterprise KYC in Must

### Must acceptance criteria

- New listing cannot publish without verified phone, location, valid price (products), and ≥2 photos
- WhatsApp CTA does not appear for unverified sellers / contacts Midora does not trust yet (exact rule: verified phone on the acting seller account)
- Listing and shop pages show Joined + Last active / Live now
- Buyer can report a listing, report a seller, and block a seller
- Existing report-listing and moderation still work

---

## Phase 1 — Should (next 2–4 weeks after Must)

- [x] Soft **profile photo** nudge before first listing or first contact
- [x] **Near-duplicate** detection: reuse pHash across active listings + same-seller title/price re-post *(migration `040_listing_image_hashes.sql` — apply in Supabase)*
- [x] Expand **spam keywords** for jobs / fake “opportunities” / stuffed brand lists
- [x] Admin **queue** for reported items + near-dupes (`/admin/reports` tabs + `/api/v1/admin/trust-queue`)
- [x] AI listing quality check stays **Improve / coach**, not a hard publish wall (unless quality is critically low)

---

## Phase 2 — Growth / reputation

- Product & service reviews depth (already partially present)
- Public **response rate / speed** once a seller has enough leads (e.g. hide until N ≥ 5)
- Active / completed offers counts when tracking is reliable
- Optional composite **Midora Trust Score** badge (only when inputs are honest and explained)
- Referral / invite for liquidity (growth, not trust foundation)
- Paid boost UX polish
- Seed a few **dense categories** rather than spreading thin across a huge taxonomy

---

## Phase 3 — Scale / verification ladder

**Status:** Shipped on main (24 Sep 2026 EAT) — extend existing `shop_verifications` / `trust_badges` ladder; do not rebuild.

Keep the staged model from the notes:

**Stage 1 — Capture, don’t manually verify everyone**

- [x] Optional selfie + ID photo stored (`request_review=false` → status `submitted`)
- [x] UI: “ID submitted / verification pending”
- [x] No manual review of every ID or business document (admin default queue = `pending` only)

**Stage 2 — Verify only when needed**

Triggers wired:

- [x] High listing volume: Identity Verified required after **5** products (`payments/plan_service.py`)
- [x] Paid packages: Standard / Premium subscribe requires Identity Verified
- [x] Business / Professional stages still require Identity Verified first
- [x] Merchant can **Request review** from captured docs anytime

Verify then:

- National ID / passport / driving permit → **Identity Verified** (`identity_verified`)
- Business registration / TIN / contacts → **Verified Business** (`business_verified`)
- Professional credentials where relevant → **Verified Professional** (`professional_verified`)

Manual review: `/admin/verifications` review queue (`pending`). Capture-only rows live under “Captured (no review)”.

Migration: `db/migrations/041_verification_ladder.sql` (adds `submitted` status).

---

## Explicitly out of Phase 1 Must

| Item | Why deferred |
|------|----------------|
| Min 3 photos as hard rule | Start at ≥2 + higher max; tighten later if needed |
| AI reverse-image vs all of Google | Costly; near-dupe in catalog first (Should) |
| Response rate / completed offers badges | Need volume; Phase 2 |
| Composite trust score as primary UX | Opaque early; Phase 2 |
| Mandatory ID / business KYC | Kills chicken-and-egg; Phase 3 unlock |
| Referral abuse tooling | No referral system yet |
| Full empty-category AI classification | Taxonomy suggestion exists; liquidity is seeding + posting speed |

---

## Chicken-and-egg (paired with trust)

Trust alone will not fill Midora. Pair Phase 1 Must with:

1. **Fast posting** (< 60 seconds target) — keep post-item flow short
2. **Feed that stays full and fair** — recent P0/P1 feed fixes (placement fill, guest diversity, category server filter, tighter new-seller)
3. **Dense categories first** — concentrate supply where buyers already search (phones, fashion, etc.) instead of empty shelves across 200+ categories
4. **Seller response habits** — track leads now; show rate later when N is enough

WhatsApp / Facebook / Jiji win on habit. Midora wins if sellers get replies and buyers see who is real and online.

---

## Suggested build order (Must)

1. Publish gates: phone verified + location + price + ≥2 photos + raise max images  
2. Surface Joined + Last active on listing/shop  
3. Report seller + block seller  
4. Phone uniqueness / conflict UX polish  
5. Regression pass: moderation, WhatsApp CTA, existing report listing  

---

## Tracking

| Field | Value |
|-------|--------|
| Locked | Yes — 22 Sep 2026 |
| Repos | `midoraonline/midoraonline`, `midoraonline/midoraonlineapi` |
| Doc path | `docs/PHASE1_TRUST_PLAN.md` (this file, under Midora project root) |
| Implementation | Must + Should on main. **Phase 3 verification ladder shipped 24 Sep 2026.** |

When implementing, prefer local work on Joel’s machine (no cloud agent unless requested). Small PRs / commits on `main` after review, same as recent feed/trust ship style.

---

## One-line summary

**Phase 1 Must = enforce real phone, place, price, photos; show Joined/Last active; report/block; keep moderation. Everything else waits.**
