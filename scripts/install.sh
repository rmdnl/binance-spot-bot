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

if [[ -L .env ]]; then
  echo ".env must be a regular file, not a symlink." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from the safe template. Set APP_SECRET_KEY and ADMIN_PASSWORD_HASH before running in production." >&2
fi

# Existing .env files may have been created manually with permissive modes.
# Always enforce owner-only access before installing dependencies or starting the app.
chmod 600 .env

.venv/bin/python -m compileall -q dca_bot
.venv/bin/pytest
printf '\nInstalled dependencies and ran the test suite. Configure .env before starting the service.\n'
