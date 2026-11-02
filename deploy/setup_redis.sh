#!/usr/bin/env bash
# One-time: install Redis on the droplet (localhost only, password, LRU eviction)
# and point the midoraapi service at it via REDIS_URL. Safe to re-run.
set -euo pipefail

HOST="${MIDORA_DEPLOY_HOST:-root@161.35.75.13}"
MAXMEMORY="${REDIS_MAXMEMORY:-256mb}"

ssh "$HOST" "MAXMEMORY=${MAXMEMORY} bash -s" <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq redis-server

CONF=/etc/redis/redis.conf
set_conf() {
  if grep -qE "^#?\s*$1 " "$CONF"; then
    sed -i -E "s|^#?\s*$1 .*|$1 $2|" "$CONF"
  else
    echo "$1 $2" >> "$CONF"
  fi
}
set_conf bind "127.0.0.1 ::1"
set_conf protected-mode yes
set_conf supervised systemd
set_conf maxmemory "$MAXMEMORY"
set_conf maxmemory-policy allkeys-lru

if grep -q '^requirepass ' "$CONF"; then
  PASS=$(awk '/^requirepass /{print $2}' "$CONF")
else
  PASS=$(openssl rand -hex 24)
  echo "requirepass $PASS" >> "$CONF"
fi

systemctl enable redis-server >/dev/null
systemctl restart redis-server
redis-cli -a "$PASS" --no-auth-warning ping

ENVFILE=$(systemctl show midoraapi -p EnvironmentFiles --value | awk '{print $1}' | sed 's/^-//')
if [ -z "$ENVFILE" ]; then
  echo "midoraapi has no EnvironmentFile. Add this line to its environment yourself:"
  echo "REDIS_URL=redis://:<password from $CONF>@127.0.0.1:6379/0"
  exit 1
fi
URL="redis://:${PASS}@127.0.0.1:6379/0"
if grep -q '^REDIS_URL=' "$ENVFILE"; then
  sed -i "s|^REDIS_URL=.*|REDIS_URL=${URL}|" "$ENVFILE"
else
  echo "REDIS_URL=${URL}" >> "$ENVFILE"
fi

echo "midoraapi start command:"
systemctl show midoraapi -p ExecStart --value | sed 's/.*argv\[\]=//; s/ ;.*//'
systemctl restart midoraapi
sleep 4
curl -sf http://127.0.0.1:8000/api/v1/health && echo
REMOTE
