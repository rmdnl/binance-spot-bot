#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dca-bot}"
PYTHON="${PYTHON:-python3}"

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this installer as the dedicated non-root dca-bot service user." >&2
  exit 1
fi

cd "${APP_DIR}"
"${PYTHON}" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
mkdir -p data logs
chmod 700 data logs

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env from the safe template. Set APP_SECRET_KEY and ADMIN_PASSWORD_HASH before running in production." >&2
fi

.venv/bin/python -m compileall -q dca_bot
.venv/bin/pytest
printf '\nInstalled dependencies and ran the test suite. Configure .env before starting the service.\n'
