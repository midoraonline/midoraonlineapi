#!/usr/bin/env bash
# Redeploy midoraapi to the droplet: syncs the working tree, reinstalls
# deps if requirements.txt changed, and restarts the systemd service.
set -euo pipefail

HOST="${MIDORA_DEPLOY_HOST:-root@161.35.75.13}"
REMOTE_DIR="/opt/midoraapi"

cd "$(dirname "$0")/.."

rsync -az --delete \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.env' --exclude='.env.local' --exclude='.pytest_cache' --exclude='*.egg-info' \
  --exclude='deploy' --exclude='api.env' \
  -e ssh ./ "${HOST}:${REMOTE_DIR}/"

# Server .env has production-only keys, so only WHATSAPP_* keys are merged in from api.env.
WA_ENV="$(grep -E '^WHATSAPP_[A-Z_]+=' api.env || true)"
if [[ -n "$WA_ENV" ]]; then
  printf '%s\n' "$WA_ENV" | ssh "$HOST" "
    set -e
    cp ${REMOTE_DIR}/.env /root/midoraapi.env.bak
    { grep -vE '^(WHATSAPP_[A-Z_]+|AFRICASTALKING_WHATSAPP_NUMBER)=' /root/midoraapi.env.bak || true; cat; } > /root/midoraapi.env.new
    install -m 600 -o midora -g midora /root/midoraapi.env.new ${REMOTE_DIR}/.env
    rm /root/midoraapi.env.new
  "
fi

ssh "$HOST" "
  set -e
  chown -R midora:midora ${REMOTE_DIR}
  sudo -u midora ${REMOTE_DIR}/.venv/bin/pip install -q -r ${REMOTE_DIR}/requirements.txt
  systemctl restart midoraapi
  sleep 4
  systemctl --no-pager --full status midoraapi | head -10
  curl -sf http://127.0.0.1:8000/api/v1/health && echo
"
