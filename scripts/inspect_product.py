"""Inspect a specific product's score components.

Usage:
    source .venv/bin/activate
    PYTHONPATH="$PWD" python scripts/inspect_product.py <product_id_or_index>
    
If called with an index (1-14), inspects the Nth product.
If called with a UUID, inspects that product.
"""

import sys
from datetime import datetime, timezone

from db.supabase import get_supabase_admin


def inspect_product(product_id: str) -> None:
    admin = get_supabase_admin()

    # Product details
    r = admin.table("products").select("*").eq("id", product_id).execute()
    if not r.data:
        print(f"Product {product_id} not found")
        return
    p = r.data[0]
    
    created = p.get("created_at", "")
    now = datetime.now(timezone.utc)
    if created:
        created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        hours_ago = (now - created_dt).total_seconds() / 3600
    else:
        hours_ago = 9999
    
    recency = max(0, min(50.0 - (hours_ago * 50.0 / 168.0), 50.0))
    
    print(f"Product: {p.get('title')}")
    print(f"  id: {product_id}")
    print(f"  created: {created} ({hours_ago:.0f}h ago)")
    print(f"  view_count: {p.get('view_count', 0)}")
    print(f"  listing_score: {p.get('listing_score', 0)}")
    print(f"  status: {p.get('status')}")
    print(f"  shop_id: {p.get('shop_id')}")
    print(f"  computed recency_score: {recency:.2f}")
    
    # Likes count
    rl = admin.table("product_likes").select("*", count="exact").eq("product_id", product_id).execute()
    likes = rl.count if hasattr(rl, "count") else len(rl.data or [])
    print(f"  likes: {likes}")
    
    # Listing events breakdown
    events = ["viewed", "whatsapp_clicked", "call_clicked", "saved", "shared", "reported", "updated", "messaged"]
    for ev in events:
        try:
            re = admin.table("listing_events").select("*", count="exact").eq("listing_id", product_id).eq("event_type", ev).execute()
            cnt = re.count if hasattr(re, "count") else len(re.data or [])
        except Exception:
            cnt = 0
        print(f"  events[{ev}]: {cnt}")
    
    # Shop info
    shop_id = p.get("shop_id")
    if shop_id:
        rs = admin.table("shops").select("trust_score,fraud_score,seller_score").eq("id", shop_id).execute()
        if rs.data:
            s = rs.data[0]
            print(f"  shop trust: {s.get('trust_score', 0)}")
            print(f"  shop fraud: {s.get('fraud_score', 0)}")
            print(f"  shop seller_score: {s.get('seller_score', 0)}")
    
    # Boost info
    rb = admin.table("listing_boosts").select("*").eq("listing_id", product_id).eq("active", True).execute()
    boosts = rb.data or []
    if boosts:
        bonus = sum(b.get("score_bonus", 0) for b in boosts)
        print(f"  active boosts: {len(boosts)}, bonus: {bonus}")
    
    # Manual score computation
    log_views = (__import__("math").log2(max(p.get("view_count", 0) or 0, 1)) * 3.0)
    log_views_capped = min(log_views, 45.0)
    likes_score = min(likes, 50) * 2.0
    
    # Check listing_events for engagement
    def count_events(event_type: str) -> int:
        try:
            r = admin.table("listing_events").select("*", count="exact").eq("listing_id", product_id).eq("event_type", event_type).execute()
            return r.count if hasattr(r, "count") else len(r.data or [])
        except Exception:
            return 0
    
    wa = count_events("whatsapp_clicked")
    saves = count_events("saved")
    shares = count_events("shared")
    msgs = count_events("messaged")
    reports = count_events("reported")
    
    wa_score = min(wa, 30) * 4.0
    saves_score = min(saves, 30) * 3.0
    shares_score = min(shares, 20) * 2.0
    msgs_score = min(msgs, 20) * 3.0
    
    trust = s.get("trust_score", 0) if shop_id else 0
    fraud = s.get("fraud_score", 0) if shop_id else 0
    
    reports_penalty = min(reports, 20) * 15.0
    fraud_penalty = fraud * 20.0
    
    total = recency + log_views_capped + likes_score + wa_score + saves_score + shares_score + msgs_score + trust * 10 - reports_penalty - fraud_penalty
    total = max(0, round(total))
    
    print(f"\n  --- Score breakdown ---")
    print(f"    recency:          {recency:>6.2f}")
    print(f"    views ({p.get('view_count',0)}):         {log_views_capped:>6.2f}")
    print(f"    likes ({likes}):           {likes_score:>6.2f}")
    print(f"    whatsapp ({wa}):          {wa_score:>6.2f}")
    print(f"    saves ({saves}):           {saves_score:>6.2f}")
    print(f"    shares ({shares}):          {shares_score:>6.2f}")
    print(f"    messages ({msgs}):         {msgs_score:>6.2f}")
    print(f"    trust ({trust}):           {trust * 10:>6.2f}")
    print(f"    reports ({reports}):        -{reports_penalty:>6.2f}")
    print(f"    fraud ({fraud}):          -{fraud_penalty:>6.2f}")
    print(f"    {'─'*32}")
    print(f"    TOTAL:            {total:>6.0f}")
    print(f"    DB listing_score: {p.get('listing_score', 0)}")


def main() -> None:
    admin = get_supabase_admin()
    
    if len(sys.argv) < 2:
        # List all products
        r = admin.table("products").select("id,title,listing_score,view_count").eq("is_published", True).order("listing_score", desc=True).execute()
        for i, p in enumerate((r.data or []), 1):
            print(f"  {i:>2}. [{p.get('listing_score',0):>3}] {str(p['id'])[:8]}...  {p.get('title','?')[:40]}")
        print(f"\nUsage: python scripts/inspect_product.py <id|number>")
        return
    
    arg = sys.argv[1]
    
    # Check if it's a number (index)
    try:
        idx = int(arg)
        r = admin.table("products").select("id").eq("is_published", True).order("listing_score", desc=True).execute()
        products = r.data or []
        if 1 <= idx <= len(products):
            product_id = products[idx - 1]["id"]
            inspect_product(product_id)
        else:
            print(f"Index {idx} out of range (1-{len(products)})")
    except ValueError:
        inspect_product(arg)


if __name__ == "__main__":
    main()
