# How the Midora Home Feed Works

One shared order for everyone, rebuilt on reload. No taste profiles, no per-user
ranking. Rules tighten as the marketplace grows (Discovery & Feed Strategy v4.0).

## 1. Pipeline

```
Active listings (newest 1,000)
  → Eligibility gates (live shop, no high/critical fraud, quality ≥ 40)
  → Growth stage (eligible count per type)
  → Score 0–100 per listing
  → Placement in 20-card blocks
  → Shared snapshot (5 min, versioned)
  → Per request: optional followed-shop lift, paid slot, page slice
```

Code: `feed/service.py` (build + serve), `quality.py`, `stages.py`, `scoring.py`,
`placement.py`, `boost_slots.py`, `snapshots.py`, `status.py`. Every number below is in
`feed/config.py` and can be changed from Admin → Feed tuning.

## 2. Growth stages

| Stage | Eligible listings | Type mix | Exploration per 20 | Paid per 20 |
|---|---|---|---|---|
| 0 | 0 | – | – | – |
| 1 | 1–49 | no | 0 | 0 |
| 2 | 50–199 | no | 0 | 0 |
| 3 | 200–499 | 60/25/15 if every type is deep | 1 | 1 |
| 4 | 500–999 | same | 2 | 1 |
| 5 | 1,000–4,999 | same, engagement starts counting | 2 | 1 |
| 6 | 5,000+ | same | 2 | 1 |

A type is deep when it has at least 3 blocks' worth of its target
(products 12, services 5, opportunities 3 per block). Thin types never get forced.

## 3. Listing quality (0–100)

| Part | Points |
|---|---|
| Media | Product: 2+ photos 30, 1 photo 20, none 0. Service/opportunity: 30 (cover is generated) |
| Value | Product price 15. Service price or quote 15, else 8. Opportunity price, deadline or pay 15, else 8 |
| Title | ≥10 chars 20, ≥3 chars 10 |
| Description | ≥80 chars 20, ≥30 chars 10 |
| Location | 10 |
| Category | 5 |

Below 40 the listing stays out of the Home feed until it is improved.

## 4. Score (0–100)

| Signal | Weight | Meaning |
|---|---|---|
| Quality | 40 | Section 3 |
| Freshness | 30 | Halves every 72 h since posting or repost |
| Trust | 15 | Shop trust score, seller score, verification |
| Engagement | 10 | `listing_score`, only from stage 5 |
| Exposure balance | 5 | Lower for shops shown very often in the last 24 h |

Medium fraud flags subtract 20. `listing_score` no longer contains boost points
(migration 062), so payment never hides inside relevance. Premium plans buy tools, not rank.

## 5. Placement

- Cards are placed in blocks of 20, best score first.
- Soft seller rules: at most 2 cards per seller per block and no seller twice in a row.
  When supply is thin the rules relax (spacing first, then the cap) instead of ending the feed.
- From stage 3, when types are deep, each pick goes to the type furthest below its block target.
- Exploration slots (stage 3+) go to quality ≥ 60 listings with under 50 impressions in 24 h,
  preferring fresh and high-quality ones. If none qualify, the slot stays organic.

## 6. Paid boosts

- Boosts only place from stage 3 (200+ eligible listings). Below that they are stored but have no effect.
- One boosted card per 20, at position 6 of each block. The ad must have quality ≥ 60 and no high/critical fraud.
- Bigger packages get more turns in the rotation (one turn per 25 points of `score_bonus`).
- A boosted ad already on the page organically is not repeated. Filters are hard: on filtered pages only
  boosts that match every filter can fill the slot.
- The card shows "Boosted" only when it sits in a paid slot.

### Sponsored

- Sponsored rows live in `listing_boosts` with `kind='sponsored'` (packages: `boost_plans.kind`, migration 064).
- Sponsored and Boosted rank in separate queues. Each gets at most one slot per 20: Boosted at
  `PAID_SLOT_POSITION` (6), Sponsored at `SPONSORED_SLOT_POSITION` (14). An empty queue leaves its slot organic.
- Same gates as boosts: stage 3+, quality ≥ 60, no high/critical fraud, never twice on a page.

## 7. What changes per viewer

- **Followed shops (toggle `FOLLOWED_SHOP_LIFT`):** inside each block, organic cards from shops the viewer
  follows get +`FOLLOWED_SHOP_BOOST` and move up. They never leave their block, so variety and type mix hold.
- Nothing is hidden per viewer: no seen-listing fatigue and no daily repeat caps, so a reload shows the same
  order. The order only moves when the shared snapshot is rebuilt (freshness, including reposts, and quality).
- Likes, views, saves and searches are recorded for analytics and engagement, but they do not reorder the feed.

## 8. Reload and paging

- The order changes only when the snapshot is rebuilt: every 5 minutes, after any listing or boost change,
  after a feed-config change, or by the daily cron.
- `next_cursor` is `v:<version>:p:<page>`. Load-more reads the same version even if a newer one exists,
  so scrolling never skips or repeats. A reload starts from the newest version.
- Page size is 40 (two blocks).

## 9. Card badge

One status per card, in this order: Sponsored → Boosted (paid slot) → Closing soon (opportunity, deadline within 7 days)
→ Trending → New (first posted within 72 h; reposts do not renew it) → none. Type is shown by the cover, not as a badge.

Trending is computed at snapshot build (`feed/trending.py`) from the last `TRENDING_WINDOW_HOURS`:
`unique viewers + 2×likes + 3×(WhatsApp + call clicks) + 4×messages − 5×reports`, divided by
`(1 + age_hours/24)^1.5`. A listing needs `TRENDING_MIN_UNIQUE_VIEWERS` and `TRENDING_MIN_ACTIONS`; the top
`TRENDING_TOP_SHARE` of each type lane qualifies.

## 10. Badge filters and locations

- `badge=new_today|trending|boosted|sponsored` on `/feed/home`, `/feed/latest` and search. On Boosted/Sponsored
  pages every card carries that disclosure.
- `GET /feed/locations?limit=` returns the most common listing places (10 min cache) for the location picker.

## 11. Not built yet

Sponsored viewer-relevance score and pacing, Featured Shops module, stage-0 demand capture
(requests, notify-me), adaptive exploration at 1,000+, and the dedicated Search/Category ranking rules.
