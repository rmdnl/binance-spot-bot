# VPS deployment guide

This guide matches the current `binance-spot-bot` source tree. The application is a FastAPI dashboard/API with Binance Spot trading support. **Live order submission remains disabled by default.** Do not enable live trading until the release has passed the full test and operational review.

## Requirements

- Ubuntu 22.04/24.04 or similar
- Python 3.11+
- Git
- A dedicated non-root service account
- A writable `data/` and `logs/` directory under `/opt/binance-spot-bot`
- Binance API credentials only when exchange connectivity is required
- Optional: Cloudflare Tunnel + Cloudflare Access for public dashboard access

## Source layout and runtime entrypoint

The production ASGI application is:

```text
spot_bot.main:app
```

The repository uses a Python virtual environment at `.venv`. The systemd unit in `deploy/binance-spot-bot.service` starts Uvicorn against this exact entrypoint on `127.0.0.1:8000`.

Do not substitute the old `dca_bot.main:app` entrypoint or `/opt/dca-bot` paths. Those belong to an older project layout and are not valid for this repository.

## Install

Create the service account and clone the repository:

```bash
sudo useradd --system --create-home --home-dir /opt/binance-spot-bot --shell /usr/sbin/nologin binance-spot-bot
sudo git clone https://github.com/rmdnl/binance-spot-bot.git /opt/binance-spot-bot
sudo chown -R binance-spot-bot:binance-spot-bot /opt/binance-spot-bot
```

Create the virtual environment and install the project:

```bash
sudo -u binance-spot-bot bash -lc '
  cd /opt/binance-spot-bot
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -e .
'
```

Create runtime directories and keep them outside version-controlled configuration:

```bash
sudo install -d -o binance-spot-bot -g binance-spot-bot -m 0700 /opt/binance-spot-bot/data /opt/binance-spot-bot/logs
```

## Environment configuration

Create `/opt/binance-spot-bot/.env` from the repository example if present, then review every value before startup. The important safety controls are:

```ini
DEFAULT_ENVIRONMENT=testnet
ALLOW_ORDER_SUBMISSION=false
```

Keep `ALLOW_ORDER_SUBMISSION=false` for the initial VPS deployment. Never put Binance API keys in source code, frontend assets, commits, or issue comments.

For dashboard authentication, configure the current source's authentication variables, including `DASHBOARD_PASSWORD_HASH` and `DASHBOARD_SESSION_SECRET`, using the exact names documented by `.env.example` and the application settings. Generate secrets with a cryptographically secure random generator and store `.env` with mode `0600` owned by the service account.

```bash
sudo chown binance-spot-bot:binance-spot-bot /opt/binance-spot-bot/.env
sudo chmod 600 /opt/binance-spot-bot/.env
```

If the environment file is absent, do not invent variable names. Inspect `.env.example` and the settings module first:

```bash
cd /opt/binance-spot-bot
sed -n '1,240p' .env.example
sed -n '1,260p' spot_bot/config.py
```

## Database migration

Run Alembic migrations as the application service user before starting the service:

```bash
sudo -u binance-spot-bot bash -lc '
  cd /opt/binance-spot-bot
  .venv/bin/alembic upgrade head
'
```

Verify the migration state:

```bash
sudo -u binance-spot-bot bash -lc '
  cd /opt/binance-spot-bot
  .venv/bin/alembic current
'
```

The migration chain must resolve cleanly to `head`. Do not manually create tables that are managed by Alembic. Before applying migrations to an existing production database, take a database backup and verify the current revision.

## Pre-start validation

Run the repository's checks from the same virtual environment that systemd will use:

```bash
cd /opt/binance-spot-bot
.venv/bin/pytest -q
.venv/bin/ruff check .
```

Do not continue to deployment if either command fails.

## Smoke test

First verify that the application imports successfully:

```bash
sudo -u binance-spot-bot bash -lc '
  cd /opt/binance-spot-bot
  .venv/bin/python -c "from spot_bot.main import app; print(app.title)"
'
```

Start a temporary local instance without exposing it publicly:

```bash
sudo -u binance-spot-bot bash -lc '
  cd /opt/binance-spot-bot
  .venv/bin/uvicorn spot_bot.main:app --host 127.0.0.1 --port 8000
'
```

From another shell on the VPS, check the local HTTP response using the routes implemented by the current application. Do not assume an endpoint from an older build. Inspect `spot_bot/main.py` and `spot_bot/trading_routes.py` if the route is unclear:

```bash
curl -i http://127.0.0.1:8000/
```

Confirm in the application logs that startup completed without database/configuration errors. Stop the temporary server before installing systemd.

## systemd installation

The repository contains the production unit:

```text
deploy/binance-spot-bot.service
```

Install it:

```bash
sudo cp /opt/binance-spot-bot/deploy/binance-spot-bot.service /etc/systemd/system/binance-spot-bot.service
sudo systemctl daemon-reload
sudo systemctl enable binance-spot-bot
sudo systemctl start binance-spot-bot
```

Verify:

```bash
sudo systemctl status binance-spot-bot --no-pager
sudo journalctl -u binance-spot-bot -n 100 --no-pager
```

The service intentionally binds Uvicorn to `127.0.0.1:8000`. Keep the origin port private. The unit also runs as the dedicated `binance-spot-bot` user and applies systemd hardening. If the service cannot write to runtime data/log paths, fix ownership rather than running it as root.

## Safe first deployment

The first VPS boot must remain in a non-live configuration:

```ini
DEFAULT_ENVIRONMENT=testnet
ALLOW_ORDER_SUBMISSION=false
```

After startup, verify that the dashboard/API works, database migrations are current, and logs contain no unexpected order-submission activity. A successful health check does **not** authorize live trading.

Before enabling real Spot orders, separately verify Binance API permissions, symbol/market metadata handling, quantity and price precision, minimum notional, fee-aware profitability, idempotency, partial fills, reconciliation, cancel/fill races, kill-switch behavior, and recovery after process/database restart. The live-order switch must remain explicitly disabled until those checks are approved.

## Optional public dashboard access

For remote dashboard access, prefer Cloudflare Tunnel + Cloudflare Access rather than exposing TCP 8000 directly to the Internet.

The application should continue listening on `127.0.0.1:8000`. Configure the tunnel to forward the chosen hostname to `http://127.0.0.1:8000`, and put a restrictive Cloudflare Access policy in front of it. Keep MFA enabled and do not expose the VPS origin port publicly.

## Operational commands

```bash
# Service status
sudo systemctl status binance-spot-bot --no-pager

# Recent application logs
sudo journalctl -u binance-spot-bot -n 100 --no-pager

# Follow logs
sudo journalctl -u binance-spot-bot -f

# Restart after an approved configuration/code update
sudo systemctl restart binance-spot-bot

# Stop safely
sudo systemctl stop binance-spot-bot
```

For code updates, pull the intended release, reinstall dependencies if `pyproject.toml` changed, run tests/lint, run `alembic upgrade head`, and only then restart the service.

## Troubleshooting

If systemd exits immediately:

```bash
sudo journalctl -u binance-spot-bot -n 200 --no-pager
```

If the application fails to import, reproduce the exact service command manually as the service user:

```bash
sudo -u binance-spot-bot bash -lc 'cd /opt/binance-spot-bot && .venv/bin/python -c "from spot_bot.main import app; print(app.title)"'
```

If migrations fail, inspect the current revision before making changes:

```bash
sudo -u binance-spot-bot bash -lc 'cd /opt/binance-spot-bot && .venv/bin/alembic current'
```

Never bypass a failed migration by deleting the database or manually changing Alembic revision records on a production installation without first taking a backup and understanding the migration state.
