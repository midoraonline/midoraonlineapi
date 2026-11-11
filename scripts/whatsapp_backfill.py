"""One-off clean-up of existing shops' WhatsApp numbers.

  python scripts/whatsapp_backfill.py                    # dry run: show what would change
  python scripts/whatsapp_backfill.py --apply            # store numbers as E.164
  python scripts/whatsapp_backfill.py --apply --check 15 # also look up at most 15 numbers

Numbers that cannot be parsed are listed and left alone (they stay hidden from buyers).
--check goes through the same cache and wa-checker limits as the app. It stops at the first
"unavailable" answer (capped, circuit open, worker down) so a back-catalogue never hammers the
linked WhatsApp account; owners are also checked lazily when they open their shop settings.
Run from the midoraapi directory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.whatsapp_number import WhatsAppNumberError, normalize_whatsapp  # noqa: E402
from db.supabase import get_supabase_admin  # noqa: E402
from shop import whatsapp_check  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--check", type=int, default=0, metavar="N", help="look up at most N unchecked numbers")
    args = parser.parse_args()

    db = get_supabase_admin()
    shops = (
        db.table("shops")
        .select("id,name,whatsapp_number,whatsapp_verified,whatsapp_registered")
        .not_.is_("whatsapp_number", "null")
        .execute()
        .data
        or []
    )
    changed = invalid = 0
    to_check: list[tuple[str, str]] = []
    for shop in shops:
        raw = (shop.get("whatsapp_number") or "").strip()
        if not raw:
            continue
        try:
            e164 = normalize_whatsapp(raw)
        except WhatsAppNumberError:
            invalid += 1
            print(f"INVALID   {shop['id']}  {shop.get('name')!r}  {raw!r}")
            continue
        if e164 != raw:
            changed += 1
            print(f"NORMALISE {shop['id']}  {raw!r} -> {e164}")
            if args.apply:
                db.table("shops").update({"whatsapp_number": e164}).eq("id", shop["id"]).execute()
        if not shop.get("whatsapp_verified") and shop.get("whatsapp_registered") is None:
            to_check.append((shop["id"], e164))

    print(f"\n{len(shops)} shops with a number | {changed} to normalise | {invalid} invalid | {len(to_check)} unchecked")
    if not args.apply:
        print("Dry run. Re-run with --apply to write.")

    done = 0
    for shop_id, e164 in to_check[: max(0, args.check)]:
        registered, source = whatsapp_check.lookup(e164)
        if registered is None:
            print("Checker unavailable or capped; stopping here.")
            break
        done += 1
        print(f"CHECKED   {shop_id}  {e164}  on_whatsapp={registered} ({source})")
        if args.apply:
            whatsapp_check.record_on_shop(shop_id, registered, source)
    if args.check:
        print(f"{done} numbers checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
