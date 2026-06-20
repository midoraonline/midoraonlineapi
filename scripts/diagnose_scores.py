"""Diagnostic script to inspect scores, boosts, and feed state.

Usage:
    source .venv/bin/activate
    python scripts/diagnose_scores.py

This uses the supabase admin client (reads from env SUPABASE_URL / SUPABASE_SERVICE_KEY).
"""

from datetime import datetime, timezone

from db.supabase import get_supabase_admin


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main() -> None:
    admin = get_supabase_admin()

    # ── 1. Product listing_score distribution ──
    section("PRODUCT LISTING_SCORE DISTRIBUTION")
    r = (
        admin.table("products")
        .select("id,title,listing_score,view_count,status,created_at,shop_id")
        .eq("status", "active")
        .eq("is_published", True)
        .order("listing_score", desc=True)
        .limit(100)
        .execute()
    )
    products = r.data or []
    print(f"Total active products scored: {len(products)}")
    print(f"{'Score':>6}  {'Views':>6}  {'Title':<40}")
    print("-" * 60)
    for p in products[:30]:
        score = p.get("listing_score", 0)
        views = p.get("view_count", 0)
        title = (p.get("title") or "?")[:40]
        print(f"{score:>6}  {views:>6}  {title}")
    if len(products) > 30:
        print(f"... and {len(products) - 30} more")

    # Score bucket summary
    buckets = {"0": 0, "1-10": 0, "11-50": 0, "51-100": 0, "101-200": 0, "201+": 0}
    for p in products:
        s = p.get("listing_score") or 0
        if s == 0: buckets["0"] += 1
        elif s <= 10: buckets["1-10"] += 1
        elif s <= 50: buckets["11-50"] += 1
        elif s <= 100: buckets["51-100"] += 1
        elif s <= 200: buckets["101-200"] += 1
        else: buckets["201+"] += 1
    print(f"\nBuckets: {buckets}")

    # ── 2. Products with zero score ──
    section("PRODUCTS WITH LISTING_SCORE = 0")
    r0 = (
        admin.table("products")
        .select("id,title,created_at,view_count,shop_id")
        .eq("status", "active")
        .eq("is_published", True)
        .eq("listing_score", 0)
        .limit(20)
        .execute()
    )
    zero_score = r0.data or []
    if zero_score:
        print(f"{len(zero_score)} active products with score=0 (showing first 20):")
        for p in zero_score:
            print(f"  {p.get('id')[:8]}...  created={str(p.get('created_at',''))[:19]}  {p.get('title','?')[:40]}")
    else:
        print("None found — all active products have a score > 0")

    # ── 3. Boost state ──
    section("BOOST STATE")
    r_b = (
        admin.table("listing_boosts")
        .select("id,listing_id,score_bonus,active,starts_at,ends_at")
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    boosts = r_b.data or []
    if boosts:
        print(f"Recent boosts (showing {len(boosts)}):")
        now = datetime.now(timezone.utc)
        expired_count = 0
        active_count = 0
        for b in boosts:
            active = b.get("active", False)
            ends = b.get("ends_at")
            is_expired = ends and datetime.fromisoformat(str(ends).replace("Z", "+00:00")) < now
            marker = ""
            if active and is_expired:
                marker = " ⚠️ STALE (active=true but ended)"
                expired_count += 1
            elif active:
                marker = " ✅ active"
                active_count += 1
            elif not active:
                marker = " ❌ expired"
            print(f"  {str(b.get('id',''))[:8]}... bonus={b.get('score_bonus')}{marker}")
        print(f"\nActive: {active_count}, Stale (active but ended): {expired_count}")
    else:
        print("No boosts found")

    # Count all stale boosts
    now_iso = datetime.now(timezone.utc).isoformat()
    r_stale = (
        admin.table("listing_boosts")
        .select("id", count="exact")
        .eq("active", True)
        .lt("ends_at", now_iso)
        .execute()
    )
    stale_count = r_stale.count if hasattr(r_stale, "count") else len(r_stale.data or [])
    print(f"\nTotal stale boosts (active=true but ended): {stale_count}")

    # ── 4. Shop scores ──
    section("SHOP SCORES")
    r_s = (
        admin.table("shops")
        .select("id,name,trust_score,fraud_score,seller_score,view_count,is_active")
        .order("seller_score", desc=True)
        .limit(20)
        .execute()
    )
    shops = r_s.data or []
    print(f"{'Trust':>6}  {'Fraud':>6}  {'Seller':>6}  {'Views':>6}  {'Name':<30}")
    print("-" * 65)
    for s in shops:
        trust = s.get("trust_score", 0)
        fraud = s.get("fraud_score", 0)
        seller = s.get("seller_score", 0)
        views = s.get("view_count", 0)
        name = (s.get("name") or "?")[:30]
        print(f"{trust:>6}  {fraud:>6}  {seller:>6}  {views:>6}  {name}")

    # ── 5. Seller reviews ──
    section("SELLER REVIEWS")
    r_rev = (
        admin.table("seller_reviews")
        .select("id,seller_id,buyer_id,rating,created_at")
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    reviews = r_rev.data or []
    print(f"Recent reviews: {len(reviews)}")
    for rv in reviews:
        print(f"  rating={rv.get('rating')}  seller={str(rv.get('seller_id',''))[:8]}...  buyer={str(rv.get('buyer_id',''))[:8]}...")

    # ── 6. Product reviews (new table) ──
    section("PRODUCT REVIEWS")
    r_pr = (
        admin.table("product_reviews")
        .select("id,product_id,user_id,rating")
        .limit(10)
        .execute()
    )
    pr = r_pr.data or []
    print(f"Product reviews found: {len(pr)}")
    for rv in pr:
        print(f"  rating={rv.get('rating')}  product={str(rv.get('product_id',''))[:8]}...")

    # ── 7. Fraud flags ──
    section("FRAUD FLAGS")
    r_f = (
        admin.table("fraud_flags")
        .select("id,seller_id,flag_type,severity,resolved")
        .limit(10)
        .execute()
    )
    flags = r_f.data or []
    print(f"Fraud flags: {len(flags)}")
    for f in flags:
        print(f"  {f.get('flag_type')}  severity={f.get('severity')}  resolved={f.get('resolved')}")

    # ── 8. Listing events summary ──
    section("LISTING EVENTS (last 7 days)")
    r_e = (
        admin.table("listing_events")
        .select("event_type", count="exact")
        .gte("created_at", "now()-interval'7 days'")
        .execute()
    )
    events = r_e.data or []
    event_counts: dict[str, int] = {}
    for e in events:
        et = e.get("event_type", "unknown")
        event_counts[et] = event_counts.get(et, 0) + 1
    if event_counts:
        for k, v in sorted(event_counts.items()):
            print(f"  {k}: {v}")
    else:
        print("No events in last 7 days")

    # ── 9. Verify DB function exists ──
    section("DB FUNCTION CHECK")
    try:
        from db.supabase import get_supabase_client
        client = get_supabase_client(None)
        r_func = client.table("products").select("listing_score").limit(1).execute()
        print("✅ Basic query works")
    except Exception as e:
        print(f"❌ Query failed: {e}")

    print("\n✅ Diagnostics complete\n")


if __name__ == "__main__":
    main()
