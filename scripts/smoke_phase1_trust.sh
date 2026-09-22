#!/usr/bin/env bash
# Phase 1 Must smoke checks against a live/local API.
# Usage: API_BASE=https://api.midoraonline.com bash scripts/smoke_phase1_trust.sh
set -euo pipefail
API_BASE="${API_BASE:-https://api.midoraonline.com}"
API_BASE="${API_BASE%/}"
pass=0; fail=0
check() {
  local name="$1"; shift
  if "$@"; then echo "PASS  $name"; pass=$((pass+1)); else echo "FAIL  $name"; fail=$((fail+1)); fi
}

echo "API_BASE=$API_BASE"
check "health" bash -c "curl -fsS \"$API_BASE/api/v1/health\" | grep -q '\"status\":\"ok\"'"
check "block seller route exists (401 unauth)" bash -c "code=\$(curl -sS -o /dev/null -w '%{http_code}' -X POST '$API_BASE/api/v1/sellers/00000000-0000-0000-0000-000000000001/block'); [[ \$code == 401 || \$code == 403 ]]"
check "report seller route exists (auth/validation)" bash -c "code=\$(curl -sS -o /dev/null -w '%{http_code}' -X POST '$API_BASE/api/v1/sellers/00000000-0000-0000-0000-000000000001/reports?reason=Spam'); [[ \$code == 401 || \$code == 403 || \$code == 400 || \$code == 422 ]]"
check "blocked-sellers list exists (401)" bash -c "code=\$(curl -sS -o /dev/null -w '%{http_code}' '$API_BASE/api/v1/me/blocked-sellers'); [[ \$code == 401 || \$code == 403 ]]"
check "admin seller-reports exists (401)" bash -c "code=\$(curl -sS -o /dev/null -w '%{http_code}' '$API_BASE/api/v1/admin/seller-reports'); [[ \$code == 401 || \$code == 403 ]]"
check "admin reports exists (401)" bash -c "code=\$(curl -sS -o /dev/null -w '%{http_code}' '$API_BASE/api/v1/admin/reports'); [[ \$code == 401 || \$code == 403 ]]"
check "product detail exposes shop trust fields" bash -c "
  pid=\$(curl -fsS '$API_BASE/api/v1/products/trending?limit=1' | python3 -c 'import json,sys; print(json.load(sys.stdin)[0][\"id\"])')
  curl -fsS \"$API_BASE/api/v1/products/\$pid\" | python3 -c 'import json,sys; s=json.load(sys.stdin).get(\"shop\") or {}; assert \"created_at\" in s and \"last_seen_at\" in s and \"owner_phone_verified\" in s'
"
check "whatsapp gated field present (owner_phone_verified)" bash -c "
  pid=\$(curl -fsS '$API_BASE/api/v1/products/trending?limit=1' | python3 -c 'import json,sys; print(json.load(sys.stdin)[0][\"id\"])')
  curl -fsS \"$API_BASE/api/v1/products/\$pid\" | python3 -c 'import json,sys; s=json.load(sys.stdin).get(\"shop\") or {}; assert \"owner_phone_verified\" in s'
"
echo "---"
echo "passed=$pass failed=$fail"
# Code-path notes (auth required for publish gates / phone_taken):
echo "NOTE: Publish gates (photos/location/price/phone) require auth — covered by shop/test_publish_gates.py + FE listingForm.ts"
echo "NOTE: phone_taken returns 409 code=phone_taken from /auth/phone/send-code and /auth/phone/verify"
[[ "$fail" -eq 0 ]]
