# Binance Spot Terminal

A self-hosted Binance Spot trading dashboard. The dashboard is the source of truth for trading configuration. `.env` contains credentials and environment secrets only.

## Scope

- Binance Spot only. No futures, margin, leverage, or autonomous pair discovery.
- Manual Spot trading: market, limit, and post-only limit orders.
- Manual Grid Bot: arithmetic or geometric grids configured from the dashboard.
- Portfolio, balances, realized PNL, unrealized PNL, fees, trades, and open orders.
- Risk controls and a fail-closed execution boundary.
- Testnet and Live credentials are isolated.
- One `main` branch is the development/deployment path.

## Environment

```env
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_API_SECRET=
BINANCE_LIVE_API_KEY=
BINANCE_LIVE_API_SECRET=
DASHBOARD_PASSWORD_HASH=
DATABASE_URL=sqlite+aiosqlite:///./data/spot_terminal.db
```

No trading parameters belong in `.env`. Pair, range, grid count, capital, fees, and risk limits are persisted from the dashboard.

## Safety

Live trading is opt-in and fail-closed. The backend validates environment, credentials, market rules, balance, order feasibility, and risk before submission. Browser code never receives Binance API secrets.
