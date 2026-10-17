# VPS deployment guide

This guide matches the current `binance-spot-bot` source tree. The application is a FastAPI dashboard/API with Binance Spot trading support. **Live order submission remains disabled by default.** Do not enable live trading until the release has passed the full test and operational review.

## Requirements

- Ubuntu 22.04/24.04 or similar
- Python 3.11+
- Git
- A dedicated non-root service account
- A writable `data/` and `logs/` directory under `/opt/binance-spot-bot`
- Optional: Cloudflare Tunnel + Cloudflare Access for public dashboard access

## Source layout and runtime entrypoint

The production ASGI application is:

```text
spot_bot.main:app
```

The repository's installer is `scripts/install.sh`. The production systemd unit in `deploy/binance-spot-bot.service` starts Uvicorn against this exact entrypoint on `127.0.0.1:8000`.

Do not substitute the old `dca_bot.main:app` entrypoint or `/opt/dca-bot` paths. Those belong to an older project layout and are not valid for this repository.

## Install

Create the service account and clone the repository:

```bash
sudo useradd --system --create-home --home-dir /opt/binance-spot-bot --shell /usr/sbin/nologin binance-spot-bot
sudo git clone https://github.com/rmdnl/binance-spot-bot.git /opt/binance-spot-bot
sudo chown -R binance-spot-bot:binance-spot-bot /opt/binance-spot-bot
```

Run the repository installer as the service user. Do **not** run it with `sudo`:

```bash
sudo -u binance-spot-bot bash -lc 'cd /opt/binance-spot-bot && ./scripts/install.sh'
```

The installer creates `.venv`, installs `.[dev]`, creates `data/` and `logs/`, creates `.env` from `.env.example` when absent, compiles the package, and runs the test suite. fileciteturn878file0

## Environment configuration

Edit `/opt/binance-spot-bot/.env`. The current template uses these names: `DATABASE_URL`, `DASHBOARD_PASSWORD_HASH`, `DASHBOARD_SESSION_SECRET`, `DEFAULT_ENVIRONMENT`, `ALLOW_ORDER_SUBMISSION`, `GRID_MIN_NET_PROFIT_PCT`, `GRID_MAKER_FEE_PCT`, and `GRID_SLIPPAGE_PCT`. Binance testnet/live API credentials are also defined there. fileciteturn877file0

For the initial VPS deployment, keep the safety defaults:

```ini
DEFAULT_ENVIRONMENT=testnet
ALLOW_ORDER_SUBMISSION=false
GRID_MIN_NET_PROFIT_PCT=0.30
GRID_MAKER_FEE_PCT=0.10
GRID_SLIPPAGE_PCT=0.05
```

The profitability values are **percent units**, not fractions. Set fee assumptions from the actual Binance account fee tier before relying on profitability decisions. fileciteturn877file0

Generate dashboard/session secrets with a cryptographically secure generator. Never commit `.env` or paste credentials into source control.

```bash
sudo -u binance-spot-bot bash -lc 'cd /opt/binance-spot-bot && .venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"'
sudo chmod 600 /opt/binance-spot-bot/.env
sudo chown binance-spot-bot:binance-spot-bot /opt/binance-spot-bot/.env
```

Set `DASHBOARD_PASSWORD_HASH` using the project's supported password-hash workflow. Do not invent a different variable name such as `ADMIN_PASSWORD_HASH`.

## Database migration

Run Alembic as the application service user before starting systemd:

```bash
sudo -u binance-spot-bot bash -lc 'cd /opt/binance-spot-bot && .venv/bin/alembic upgrade head'
sudo -u binance-spot-bot bash -lc 'cd /opt/binance-spot-bot && .venv/bin/alembic current'
```

The migration chain must resolve cleanly to `head`. Do not manually create tables managed by Alembic. Before migrating an existing production database, take a database backup and verify the current revision.

## Pre-start validation

Run the same checks used by CI:

```bash
cd /opt/binance-spot-bot
.venv/bin/python -m compileall -q spot_bot
.venv/bin/pytest
.venv/bin/ruff check .
```

The repository CI performs these four gates after installing `.[dev]`. fileciteturn876file0

Do not continue to deployment if any gate fails.

## Smoke test

Verify that the production ASGI object imports successfully:

```bash
sudo -u binance-spot-bot bash -lc 'cd /opt/binance-spot-bot && .venv/bin/python -c "from spot_bot.main import app; print(app.title)"'
```

Start a temporary local instance without exposing it publicly:

```bash
sudo -u binance-spot-bot bash -lc 'cd /opt/binance-spot-bot && .venv/bin/uvicorn spot_bot.main:app --host 127.0.0.1 --port 8000'
```

From another VPS shell, verify the root route responds:

```bash
curl -i http://127.0.0.1:8000/
```

For authenticated dashboard/API routes, use the routes implemented by the current `spot_bot` package rather than commands copied from an older build. Stop the temporary server before installing systemd.

## systemd installation

The repository contains the production unit:

```text
 deploy/binance-spot-bot.service
```

It runs as `binance-spot-bot`, uses `/opt/binance-spot-bot`, loads `/opt/binance-spot-bot/.env`, and starts `spot_bot.main:app` on `127.0.0.1:8000`. fileciteturn873file0

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
curl -i http://127.0.0.1:8000/
```

The service unit applies `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`, and `UMask=0077`, while allowing writes only to the application's `data/` and `logs/` paths. fileciteturn873file0

## Safe first deployment

The first VPS boot must remain non-live:

```ini
DEFAULT_ENVIRONMENT=testnet
ALLOW_ORDER_SUBMISSION=false
```

After startup, verify the dashboard/API works, migrations are current, and logs contain no unexpected order-submission activity. A successful HTTP response does **not** authorize live trading.

Before enabling real Spot orders, separately verify Binance API permissions, symbol/market metadata handling, quantity and price precision, minimum notional, fee-aware profitability, idempotency, partial fills, reconciliation, cancel/fill races, kill-switch behavior, and recovery after process/database restart. The live-order switch must remain explicitly disabled until those checks are approved.

## Optional public dashboard access

For remote dashboard access, prefer Cloudflare Tunnel + Cloudflare Access rather than exposing TCP 8000 directly to the Internet.

Keep the application listening on `127.0.0.1:8000`. Configure the tunnel to forward the chosen hostname to `http://127.0.0.1:8000`, and put a restrictive Cloudflare Access policy in front of it. Keep MFA enabled and do not expose the VPS origin port publicly.

## Operational commands

```bash
sudo systemctl status binance-spot-bot --no-pager
sudo journalctl -u binance-spot-bot -n 100 --no-pager
sudo journalctl -u binance-spot-bot -f
sudo systemctl restart binance-spot-bot
sudo systemctl stop binance-spot-bot
```

For code updates, pull the intended release, reinstall dependencies if `pyproject.toml` changed, run the CI-equivalent checks, run `alembic upgrade head`, and only then restart the service.

## Troubleshooting

If systemd exits immediately:

```bash
sudo journalctl -u binance-spot-bot -n 200 --no-pager
```

If the application fails to import, reproduce the exact application import as the service user:

```bash
sudo -u binance-spot-bot bash -lc 'cd /opt/binance-spot-bot && .venv/bin/python -c "from spot_bot.main import app; print(app.title)"'
```

If migrations fail, inspect the current revision before making changes:

```bash
sudo -u binance-spot-bot bash -lc 'cd /opt/binance-spot-bot && .venv/bin/alembic current'
```

Never bypass a failed migration by deleting the database or manually changing Alembic revision records on a production installation without first taking a backup and understanding the migration state.
