# Binance Spot Bot

Self-hosted Binance Spot trading terminal with two explicit modes: **Manual Spot Trading** and **Spot Grid Bot**.

## Fixed scope

- Spot only. No futures, margin, leverage, DCA, autonomous pair discovery, or strategy selection.
- Pair and trading parameters are chosen manually in the dashboard.
- `.env` is for API credentials, dashboard security, database and infrastructure settings only.
- Testnet and Live credentials are isolated. There is no automatic fallback.
- Realized and Unrealized PNL remain quote-currency aware.
- Dashboard supports USD and IDR equity display without mixing native accounting currencies.
- One `main` branch is the development and deployment path.

## Grid example

```text
PAIR:        BTC/USDT
LOWER:       80000
UPPER:       90000
GRIDS:       11
CAPITAL:     500 USDT
```

Grid levels are calculated from the configured range and grid type. The same Spot order lifecycle, persistence, accounting and risk gates are used by Manual Spot and Grid Bot.

## Environment

```env
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_API_SECRET=
BINANCE_LIVE_API_KEY=
BINANCE_LIVE_API_SECRET=
DASHBOARD_PASSWORD_HASH=
DASHBOARD_SESSION_SECRET=
DATABASE_URL=sqlite:///./data/spot_bot.db
DASHBOARD_COOKIE_SECURE=true
ALLOW_ORDER_SUBMISSION=false
DEFAULT_ENVIRONMENT=testnet
```

Generate the dashboard password hash with `python scripts/create_admin_hash.py`. Minimum password length is 8 characters. Use Spot-only API permissions and disable withdrawals.

## Safety

Execution remains fail-closed until exchange filters, order preview, idempotency, reconciliation, fills, balance checks, risk enforcement, and Testnet end-to-end validation are covered by tests. Do not enable Live execution merely because the dashboard loads.

## Development

```bash
pip install -e '.[dev]'
pytest
ruff check .
```
