# DCA Bot

A self-hosted Binance **Spot-only** DCA trading dashboard inspired by the workflow of established DCA platforms. It is an independent implementation and is not affiliated with 3Commas.

> **Safety status:** initial development foundation. Paper trading is the only intended default. Do not use with real funds until exchange execution, reconciliation, authentication, and risk controls have been implemented and independently tested.

## Current implementation status

The dashboard now has an authenticated read-only Binance Spot ticker endpoint and a DCA ladder/capital preview for fixed quote or fixed coin sizing. Strategy configuration includes initial controls for DCA mode, take-profit basis, stop-loss basis, breakeven, reinvestment and maximum deal duration. These settings are configuration scaffolding, not a running trading engine. No order placement is available yet.

See [FEATURE_PARITY.md](FEATURE_PARITY.md) for the target feature matrix and delivery gates, and [DEPLOYMENT.md](DEPLOYMENT.md) for the recommended Cloudflare Tunnel + Access setup.

## Goals

- Responsive dark trading dashboard served on port **8082**.
- Manually configurable trading pairs and per-strategy settings.
- Basic, Advanced, and Risk & Execution configuration levels.
- DCA deal lifecycle: base order, safety orders, average entry, take-profit, optional trailing take-profit, and configurable stop loss.
- Persistent strategy/deal state and auditable configuration changes.
- Binance Spot only: no futures, margin, leverage, or withdrawals.
- Authentication and explicit live-trading safeguards.
- Paper trading first, followed by Binance Spot Testnet integration and controlled live mode only after validations pass.

## Planned configurable strategy parameters

Pairs; quote/base order sizing; fixed or balance-relative sizing; safety-order count; safety-order volume scale; initial price deviation; deviation scale; deal limits; cooldown; take profit basis and target; trailing activation/callback; stop loss; partial exits; break-even rules; order type and timeout; fee and slippage assumptions; per-pair and total allocation caps; reserve balance; daily-loss and equity-drawdown guards; market/indicator filters; timeframe; retry policy; reconciliation cadence; notification settings; and schedules.

Not every setting should be exposed as active until its behaviour is implemented and tested. Unsupported settings must be labelled unavailable rather than pretending to work.

## Safety rules

- Never commit API keys, passwords, session secrets, environment files, account data, or database files.
- Keep API credentials on the VPS, restrict permissions and IPs, and never request withdrawal permission.
- Use least-privilege Spot API permissions. Keep live execution disabled by default.
- Require validation and explicit re-authentication/confirmation for live trading and other sensitive actions.
- Validate Binance symbol filters, minimum notional, step size, tick size, fees, and estimated capital before placing orders.
- Make order submission idempotent where possible; reconcile remote orders and trades after timeouts and restarts.
- A stop request must prevent new entries; cancellation and position liquidation are distinct operations and must not be conflated.
- Persist deal state transactionally and keep an audit trail of configuration changes and operator actions.
- Run behind HTTPS/reverse proxy for remote access; do not expose an unauthenticated dashboard to the public internet.

## Runtime

Target dashboard address: `http://127.0.0.1:8082` when accessed locally. For remote use, put it behind authenticated HTTPS at a reverse proxy. Bind publicly only when the host firewall and TLS setup are configured.

## Development status

The current scaffold includes a responsive dashboard, password login, editable strategy profiles, SQLite persistence, initial risk validation helpers, a password-hash setup utility, VPS deployment notes, and GitHub Actions CI. Automated checks have passed during development. The dashboard does not place exchange orders yet, and market data, paper deal simulation, Binance Testnet execution, reconciliation, and live trading remain future work. Consult [DEPLOYMENT.md](DEPLOYMENT.md) before installing.

## License

MIT.