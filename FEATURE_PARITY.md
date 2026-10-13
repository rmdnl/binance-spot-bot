# DCA Bot feature-parity roadmap

This is an independent implementation aiming for comparable Spot DCA workflows. It is not affiliated with 3Commas and does not copy its proprietary code. “Parity target” means user-facing behaviour, not identical internals.

## Current status

| Capability | Status | Notes |
|---|---|---|
| Password-protected dashboard and CSRF token | Implemented foundation | Add Cloudflare Access as an outer access gate before public exposure. |
| Manual symbol selection and saved strategy profiles | Implemented foundation | Autonomous paper mode must be explicitly enabled on a saved profile. |
| Public Binance Spot ticker | Implemented | Read-only CCXT adapter; no API key is used for public market data. |
| DCA ladder/capital preview | Implemented foundation | Arithmetic cumulative price-deviation ladder, position/order averaging, fixed quote and fixed coin order sizing. |
| Basic/Advanced/Risk settings | Partial | Some fields are saved but do not yet drive a running deal engine. |
| Paper deal lifecycle | Implemented foundation | Persisted base/safety fills, average entry, fee-aware simulated PnL, TP, trailing TP, SL/breakeven, duration cap, profit reinvestment, and background price polling. Needs broader state-machine/failure testing. |
| Autonomous market analysis | Implemented for paper mode | EMA/RSI/ATR/ADX, closed-candle evaluation, volatility-aware DCA spacing/TP, capital caps, decision audit, and candle-level idempotency. It may open simulated deals only; no exchange orders. |
| Binance Spot Testnet order execution | Not implemented | Must follow paper simulation and reconciliation tests. |
| Live trading | Disabled | Must remain disabled until safety gates and Testnet validation pass. |

## Target capability matrix

### Strategy creation and entry

- Spot-only long DCA; no futures, margin, leverage, or short-selling.
- One or multiple trading pairs with independent deals and configurable global/per-pair deal caps.
- Manual start, immediate start, autonomous paper entry from EMA/RSI/ATR/ADX conditions. Webhook start signals and additional indicator profiles remain pending.
- Market and limit orders, order timeout, cooldown, and exchange precision/minimum-notional validation.
- Fixed quote amount, fixed coin amount, and balance-percentage sizing.
- Position averaging and order averaging, safety-order count, volume multiplier, initial deviation, deviation multiplier, and arithmetic/geometric ladder choice.

### Deal management and exits

- Base order and sequential safety orders with idempotent order IDs and fill-aware state transitions.
- Average entry and break-even calculations from executed base quantities and actual commissions.
- Take profit based on average entry or base order, trailing take profit, and up to six configurable partial take-profit targets.
- Optional stop loss based on average entry or base order, move-stop-to-breakeven, trailing stop loss, and configurable close/stop behaviour.
- Deal timeout, optional profit reinvestment, manual close, cancel pending orders, and pause/stop strategy controls.

### Monitoring and risk

- Active deals, open orders, trade history, realized/unrealized PnL, fees, balances, and equity.
- Total allocation cap, per-deal capital cap, reserve balance, daily loss limit, equity drawdown kill switch, and exchange availability checks.
- Reconciliation against Binance after restart and on a schedule; unknown or mismatched exchange state must fail closed rather than duplicate orders.
- Audit trail for configuration changes, starts/stops, order actions, errors, and risk-triggered shutdowns.

### Operations and security

- Test mode by default, separate Testnet and live credentials, and no withdrawal permissions.
- Live mode requires server-side enablement plus a deliberate confirmation step; a UI toggle alone must never authorize it.
- Cloudflare Tunnel and Cloudflare Access, MFA on the Cloudflare identity, localhost-only origin binding, secure session cookies, rate limits, and no public origin port.
- CI, unit tests, state-machine tests, simulated exchange tests, Testnet integration tests, and documented recovery procedures.

## Delivery gates

1. DCA math and configuration validation.
2. Read-only market data and exchange symbol filters.
3. Deterministic paper engine, persisted deal state and restart serialization (in progress).
4. Broader autonomous strategy validation, fuller deal controls and detailed event history.
5. Restart recovery and idempotent reconciliation.
6. Binance Spot Testnet execution and adversarial failure tests.
7. Dashboard history, PnL and risk panels wired to complete state.
8. Security review and manual approval before any live mode implementation.

Reference behaviour from official 3Commas documentation:
- [DCA Bot setup and settings](https://help.3commas.io/en/articles/16281102-how-the-dca-bot-works-strategy-setup-guide)
- [DCA bot summary and capital requirements](https://help.3commas.io/en/articles/16666593-understanding-the-dca-bot-summary-box)
- [Order types, trailing stop and multiple take-profit targets](https://help.3commas.io/en/articles/16281159-order-types)

The references are used to define feature expectations only. The project remains independent and Spot-only.
