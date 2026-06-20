"""Test what the feed API returns for a given user.

Simulates exactly what the backend API does:
  - get_algorithm_feed (personalized)
  - get_home_feed (composite)
  - get_latest_feed (fallback)

Usage:
    source .venv/bin/activate
    PYTHONPATH="$PWD" python scripts/test_feed.py [--user USER_ID]
"""

import sys
import json
from datetime import datetime, timezone

from db.supabase import get_supabase_admin, get_supabase_client
from feed.service import get_algorithm_feed, get_latest_feed
from feed.composite import get_home_feed


def main() -> str | None:
    admin = get_supabase_admin()
    client = get_supabase_client(None)

    user_id = None
    if "--user" in sys.argv:
        idx = sys.argv.index("--user")
        if idx + 1 < len(sys.argv):
            user_id = sys.argv[idx + 1]

    print(f"Using user_id: {user_id or 'None (anonymous)'}")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print()

    # ── 1. Latest feed (always works, no personalization) ──
    print("=" * 60)
    print("  1. LATEST FEED (fallback, no personalization)")
    print("=" * 60)
    latest = get_latest_feed(client, limit=20)
    if latest:
        print(f"  {len(latest)} products:")
        for p in latest:
            print(f"    score={p.listing_score:>4}  views={p.view_count:>3}  {p.title[:40]}")
    else:
        print("  EMPTY")
    print()

    # ── 2. Algorithm feed (personalized) ──
    print("=" * 60)
    print(f"  2. ALGORITHM FEED (user={user_id or 'anonymous'})")
    print("=" * 60)
    try:
        alg = get_algorithm_feed(client, user_id=user_id, page=1, limit=20)
        if alg:
            print(f"  {len(alg)} products:")
            for p in alg:
                print(f"    score={p.listing_score:>4}  views={p.view_count:>3}  {p.title[:40]}")
        else:
            print("  EMPTY")
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
    print()

    # ── 3. Home feed (composite) ──
    print("=" * 60)
    print(f"  3. HOME FEED (user={user_id or 'anonymous'})")
    print("=" * 60)
    try:
        home = get_home_feed(limit=72, page=1, user_id=user_id)
        for key in ("algorithm", "trending", "premium", "fresh"):
            items = home.get(key, [])
            print(f"\n  [{key}] {len(items)} items:")
            for p in items[:10]:
                print(f"    score={p.get('listing_score',0):>4}  views={p.get('view_count',0):>3}  boosted={p.get('boosted',False)}  {p.get('title','?')[:35]}")
            if len(items) > 10:
                print(f"    ... and {len(items)-10} more")
        print(f"\n  page={home.get('page')}  limit={home.get('limit')}  total={home.get('total')}")
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
    print()

    # ── 4. Check user signals ──
    if user_id:
        print("=" * 60)
        print(f"  4. USER SIGNALS for {user_id[:16]}...")
        print("=" * 60)

        # Likes
        r_likes = admin.table("product_likes").select("product_id,created_at").eq("user_id", user_id).execute()
        print(f"  Likes: {len(r_likes.data or [])}")

        # Views in listing_events
        r_views = admin.table("listing_events").select("listing_id,created_at").eq("buyer_id", user_id).eq("event_type", "viewed").execute()
        print(f"  Viewed events: {len(r_views.data or [])}")

        # Saves
        r_saves = admin.table("listing_events").select("listing_id,created_at").eq("buyer_id", user_id).eq("event_type", "saved").execute()
        print(f"  Saved events: {len(r_saves.data or [])}")

        # Follows
        r_flw = admin.table("shop_follows").select("shop_id").eq("user_id", user_id).execute()
        print(f"  Followed shops: {len(r_flw.data or [])}")

        # Searches
        r_src = admin.table("search_history").select("query").eq("user_id", user_id).order("created_at", desc=True).limit(8).execute()
        if r_src.data:
            print(f"  Recent searches: {[s.get('query','') for s in r_src.data]}")
    else:
        print("(no user_id provided — skipping signal check)")
    print()

    # ── 5. Product count summary ──
    print("=" * 60)
    print("  5. DATABASE SUMMARY")
    print("=" * 60)
    r_cnt = admin.table("products").select("id", count="exact").eq("is_published", True).eq("status", "active").execute()
    total = r_cnt.count if hasattr(r_cnt, "count") else 0
    print(f"  Active published products: {total}")

    # Count products with score > 0
    r_scored = admin.table("products").select("id", count="exact").eq("is_published", True).eq("status", "active").gt("listing_score", 0).execute()
    scored = r_scored.count if hasattr(r_scored, "count") else 0
    print(f"  Products with listing_score > 0: {scored}")

    # Count boosted products
    now_iso = datetime.now(timezone.utc).isoformat()
    r_boost = admin.table("listing_boosts").select("listing_id", count="exact").eq("active", True).gte("ends_at", now_iso).execute()
    boosted = r_boost.count if hasattr(r_boost, "count") else 0
    print(f"  Active boosts: {boosted}")

    return user_id


if __name__ == "__main__":
    main()
