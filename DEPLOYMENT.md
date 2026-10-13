# VPS deployment guide

This project is an early-stage dashboard scaffold. **Exchange order execution is disabled** in this version. Do not enable live trading based on the presence of dashboard controls.

## Requirements

- Ubuntu 22.04/24.04 or similar
- Python 3.11+
- A dedicated, non-root service account
- A reverse proxy with HTTPS for remote access

## Install

Create the service user and working directory, then clone the public repository:

```bash
sudo useradd --system --create-home --home-dir /opt/dca-bot --shell /usr/sbin/nologin dca-bot
sudo -u dca-bot git clone https://github.com/rmdnl/dca-bot.git /opt/dca-bot
sudo -u dca-bot bash -lc 'cd /opt/dca-bot && bash scripts/install.sh'
```

Edit `/opt/dca-bot/.env`. Use a long random `APP_SECRET_KEY`, set the password hash using `python scripts/create_admin_hash.py`, and keep `APP_ENV=development` only for local smoke tests. The example config intentionally has no configured dashboard password until you set it.

Generate a key and hash interactively:

```bash
cd /opt/dca-bot
.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'
.venv/bin/python scripts/create_admin_hash.py
```

Copy the generated values into `.env`; never paste plaintext passwords or secrets into the repository or chat.

For production, configure at minimum:

```ini
APP_ENV=production
APP_HOST=127.0.0.1
APP_PORT=8082
APP_SECRET_KEY=<unique-random-value-at-least-32-characters>
ADMIN_PASSWORD_HASH=<generated-hash>
SESSION_COOKIE_SECURE=true
TRADING_MODE=paper
LIVE_TRADING_ENABLED=false
BINANCE_TESTNET=true
```

`SESSION_COOKIE_SECURE=true` requires HTTPS. During local-only HTTP development, it may be false. Do not expose the dashboard port publicly; proxy it through HTTPS and restrict network access.

## Start for a local smoke test

```bash
cd /opt/dca-bot
.venv/bin/uvicorn dca_bot.main:app --host 127.0.0.1 --port 8082
```

Open `http://127.0.0.1:8082/login` from the VPS or through an SSH tunnel. The initial version saves and validates strategy profiles, but does not start deals or submit exchange orders.

## systemd

Review `deploy/dca-bot.service` before installing it. Ensure the service user's permissions allow access to the project and its `data/` and `logs/` directories. Keep the SQLite database and environment file outside version control.

## Before real trading

Live trading must remain disabled until a separate, reviewed implementation covers exchange metadata validation, Spot-only order placement, idempotency, partial fills, order and trade reconciliation, transactional deal state, emergency-stop behaviour, daily-loss and equity drawdown enforcement, fee-aware PnL, and integration tests using the Binance Spot Testnet. A UI toggle alone must never activate live orders.
