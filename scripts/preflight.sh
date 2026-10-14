#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dca-bot}"
cd "${APP_DIR}"

fail() {
  echo "PREFLIGHT FAILED: $1" >&2
  exit 1
}

[[ -f .env ]] || fail ".env is missing"
[[ ! -L .env ]] || fail ".env must not be a symlink"
mode=$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env)
[[ "$mode" == "600" ]] || fail ".env permissions must be 600 (found $mode)"

[[ -x .venv/bin/python ]] || fail ".venv/bin/python is missing"
[[ -x .venv/bin/pytest ]] || fail ".venv/bin/pytest is missing"

APP_ENV=production .venv/bin/python - <<'PY'
from pathlib import Path
from dca_bot.config import Settings

settings = Settings()
if settings.app_env != "production":
    raise SystemExit("APP_ENV must be production for a deployed instance")
settings.assert_safe()
if settings.trading_mode != "paper":
    raise SystemExit("TRADING_MODE must be paper")
if settings.live_trading_enabled:
    raise SystemExit("LIVE_TRADING_ENABLED must be false")
if not settings.binance_testnet:
    raise SystemExit("BINANCE_TESTNET must remain true for paper deployment")
if settings.app_host not in {"127.0.0.1", "localhost"}:
    raise SystemExit("APP_HOST must remain localhost-only for the recommended deployment")
if not settings.session_cookie_secure:
    raise SystemExit("SESSION_COOKIE_SECURE must be true behind HTTPS")
if settings.global_capital_limit_quote <= 0:
    raise SystemExit("GLOBAL_CAPITAL_LIMIT_QUOTE must be positive")

Path("data").mkdir(exist_ok=True)
Path("logs").mkdir(exist_ok=True)
print("PREFLIGHT OK: production + paper-only + localhost origin + HTTPS cookies")
PY

.venv/bin/python -m compileall -q dca_bot
.venv/bin/pytest -q

echo "PREFLIGHT PASSED: safe to start the paper-mode service."
