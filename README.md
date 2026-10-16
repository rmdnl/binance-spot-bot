# Binance Spot Bot

A self-hosted Binance Spot terminal for manual Spot trading and Grid trading.

## Scope

- Binance Spot only: no futures, margin, leverage, autonomous pair discovery, or autonomous strategy selection.
- Dashboard-managed trading parameters. `.env` contains credentials and server secrets only.
- Manual Spot and Grid Bot are separate execution modes that will share order lifecycle, accounting, portfolio, and risk services.
- Realized and unrealized PNL are shown separately, with quote-currency-aware accounting.
- Testnet and Live API credentials are isolated; no automatic fallback between environments.
- One `main` branch is the development/deployment path.

## Environment

Copy `.env.example` to `.env` and configure:

```env
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_API_SECRET=
BINANCE_LIVE_API_KEY=
BINANCE_LIVE_API_SECRET=
DASHBOARD_PASSWORD_HASH=
DASHBOARD_SESSION_SECRET=
DATABASE_URL=sqlite+aiosqlite:///./data/spot_terminal.db
DASHBOARD_COOKIE_SECURE=true
```

Generate the password hash with:

```bash
python scripts/create_admin_hash.py
```

Use a password with at least 8 characters; a longer passphrase is recommended. Never commit `.env` or API credentials. Binance keys should have Spot trading permissions only when needed and withdrawals disabled.

## Safety status

The repository is an early foundation, **not production-ready and not authorized for Live trading**. Dashboard controls that are not implemented remain disabled. Before real orders are enabled, the project needs tested exchange rule validation, order preview, idempotent order submission/reconciliation, persistent fills and accounting, actual-balance checks, risk enforcement, live-mode confirmation, and testnet end-to-end validation.

## Development

```bash
pip install -e '.[dev]'
python -m compileall -q dca_bot
pytest
ruff check .
```
