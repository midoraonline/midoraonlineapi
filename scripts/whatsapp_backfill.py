"""One-off clean-up of existing shops' WhatsApp numbers.

  python scripts/whatsapp_backfill.py           # dry run: show what would change
  python scripts/whatsapp_backfill.py --apply   # store numbers as E.164

Numbers that cannot be parsed are listed and left alone (they stay hidden from buyers).
Unverified shops are counted; owners verify them with a WhatsApp code from shop settings.
Run from the midoraapi directory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.whatsapp_number import WhatsAppNumberError, normalize_whatsapp  # noqa: E402
from db.supabase import get_supabase_admin  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
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
    changed = invalid = unverified = 0
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
        if not shop.get("whatsapp_verified") and shop.get("whatsapp_registered") is not True:
            unverified += 1

    print(f"\n{len(shops)} shops with a number | {changed} to normalise | {invalid} invalid | {unverified} unverified")
    if not args.apply:
        print("Dry run. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
