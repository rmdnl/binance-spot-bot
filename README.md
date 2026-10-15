# DCA Bot

A self-hosted Binance **Spot-only** DCA trading dashboard. It is an independent implementation and is not affiliated with 3Commas.

> **Safety status:** development foundation. Paper trading is the current execution engine. Do not use with real funds until exchange execution, reconciliation, authentication, and risk controls have been independently validated.

## Architecture

DCA Bot is intentionally **manual and deterministic**. The operator selects the USDT pairs and configures the DCA parameters from the dashboard. The bot does not discover pairs, scan for opportunities, evaluate trading signals, or choose entries autonomously.

The lifecycle is:

1. Configure pairs and DCA parameters in the dashboard.
2. Select a configured pair manually and press **Start DCA**.
3. The base order is created by the DCA engine.
4. Safety/DCA orders are triggered only by the configured downside price ladder.
5. Average entry, take profit, optional trailing take profit, breakeven, stop loss, cooldown, price range, and capital/risk limits are evaluated by the deterministic deal engine.
6. The worker manages only already-started deals. It never creates a new deal by itself.

## Dashboard parameters

### Basic

- Pair(s), USDT spot only
- Base Order Size
- DCA Order Size
- Max DCA Orders
- Price Drop Step
- Price Deviation Multiplier
- DCA Order Size Multiplier
- Take Profit %
- Take-profit basis
- DCA sizing method
- DCA accounting mode

### Advanced

- Trigger Price
- Cooldown Between Rounds
- Price Range with minimum/maximum price
- Stop Loss and Stop Loss basis
- Stop Loss and End The Bot
- Trailing Take Profit
- Trailing activation and callback
- Move Stop to Breakeven
- Breakeven activation and offset
- Reinvest Profit
- Maximum deal duration

### Risk & execution

- Maximum capital per deal
- Maximum total allocation
- Reserve balance
- Daily loss limit
- Maximum equity drawdown
- Minimum net profit
- Fee assumption
- Slippage assumption
- Order timeout
- Reconciliation cadence

Unsupported controls must not be presented as working features. Configuration validation is fail-closed.

## DCA math

The DCA ladder is calculated from the configured base price, base order value, DCA order value, maximum DCA count, price-drop step, deviation multiplier, and order-size multiplier.

With a deviation multiplier of `1`, DCA levels are evenly spaced. A multiplier above `1` widens each successive price-drop step. Order sizing can remain fixed or scale geometrically according to the configured DCA order-size multiplier.

The engine calculates total planned quote capital and average entry before a deal is started. It rejects configurations whose planned ladder exceeds the configured capital limits or whose estimated take-profit does not meet the configured minimum net-profit threshold after fee and slippage assumptions.

## Safety rules

- Binance Spot only. No futures, margin, leverage, or withdrawals.
- Live trading remains disabled by default.
- Never commit API keys, passwords, session secrets, environment files, account data, or database files.
- Keep credentials on the VPS, restrict permissions and API scopes, and never grant withdrawal permission.
- Validate pair format, quote asset, order sizing, capital limits, fees, and slippage before starting a deal.
- Persist deal state transactionally and keep an audit trail of configuration changes and operator actions.
- Protective exits are evaluated before DCA-entry guards such as price range and cooldown.
- A stop-loss with **End The Bot** enabled latches the dashboard configuration off, preventing subsequent manual starts until the operator saves a configuration again.
- Paper mode never submits exchange orders.
- Remote dashboard access must be protected by authenticated HTTPS/reverse proxy; do not expose an unauthenticated dashboard to the public internet.

## Runtime

Target dashboard address: `http://127.0.0.1:8082` when accessed locally. For remote use, put it behind authenticated HTTPS at a reverse proxy.

## Development status

The repository currently contains the manual dashboard, persistent configuration, deterministic paper DCA lifecycle, DCA ladder math, risk guards, authentication, audit events, and paper-deal worker. Binance Spot Testnet execution and exchange-order reconciliation are separate execution work and must be validated before being enabled. No autonomous trading engine is part of the intended architecture.

See [FEATURE_PARITY.md](FEATURE_PARITY.md) for the feature matrix and [DEPLOYMENT.md](DEPLOYMENT.md) for deployment guidance.

## License

MIT.
