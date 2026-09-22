#!/usr/bin/env python3
"""Seed a dense Midora product catalog for feed/diversity QA.

Uses the same create-product path merchants use (POST /api/v1/shops/{shop_id}/products)
so publish gates + moderation still apply.

Requirements (Joel local env — do not invent secrets):
  - MIDORA_API_BASE   e.g. http://127.0.0.1:8000  or https://api.midoraonline.com
  - MIDORA_ACCESS_TOKEN  JWT access token for a merchant with a shop
  - MIDORA_SHOP_ID       shop UUID owned by that merchant
Optional:
  - SEED_CATALOG_JSON    path to JSON array (default: alongside this script)

Phone must be verified on the merchant account (Phase 1 Must), and each product
needs ≥2 photos + location + price > 0 (already in the sample JSON).

Run from midoraapi:
  python scripts/seed_catalog.py

Dry-run (print payloads only):
  python scripts/seed_catalog.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_JSON = HERE / "seed_catalog_products.json"


def _env(name: str) -> str:
    val = (os.getenv(name) or "").strip()
    if not val:
        raise SystemExit(
            f"Missing {name}. Set it in your shell or .env — see script docstring."
        )
    return val


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--sleep", type=float, default=0.35, help="Pause between creates")
    args = parser.parse_args()

    products = json.loads(args.json.read_text(encoding="utf-8"))
    if not isinstance(products, list) or not products:
        raise SystemExit(f"No products in {args.json}")

    if args.dry_run:
        print(f"Dry-run: {len(products)} products from {args.json}")
        for i, p in enumerate(products, 1):
            print(f"  {i:02d}. {p['title'][:60]} | {p['category']} | UGX {p['price_ugx']}")
        return 0

    base = _env("MIDORA_API_BASE").rstrip("/")
    token = _env("MIDORA_ACCESS_TOKEN")
    shop_id = _env("MIDORA_SHOP_ID")

    url = f"{base}/api/v1/shops/{shop_id}/products"
    ok = 0
    failed = 0
    for i, body in enumerate(products, 1):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            pid = payload.get("id") or payload.get("product_id") or "?"
            print(f"[{i}/{len(products)}] OK {pid} — {body['title'][:50]}")
            ok += 1
        except urllib.error.HTTPError as exc:
            err = exc.read().decode("utf-8", errors="replace")[:400]
            print(f"[{i}/{len(products)}] FAIL {exc.code} — {body['title'][:40]} :: {err}")
            failed += 1
        except Exception as exc:
            print(f"[{i}/{len(products)}] ERROR — {body['title'][:40]} :: {exc}")
            failed += 1
        time.sleep(max(0.0, args.sleep))

    print(f"\nDone. created={ok} failed={failed} total={len(products)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
