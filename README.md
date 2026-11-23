# BTC Race Bot — btc-race-24h-20261123-0511

[![make.fun · btc-race-24h-20261123-0511](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![rank](https://make.fun/badge/btc-race-24h-20261123-0511/endraputra9/btc-race-24h-20261123-0511/rank.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![reward](https://make.fun/badge/btc-race-24h-20261123-0511/endraputra9/btc-race-24h-20261123-0511/reward.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)

A long-only selective breakout rider trading CBBTC/USDC on 1-minute bars
for the make.fun `btc-race-24h-20261123-0511` competition. It buys only a
confirmed breakout — close clears the 60-bar high by ≥ 0.12%, price above
a non-falling EMA(120), force without exhaustion (30-bar return ≥ +0.30%,
60-bar ≤ +1.6%, 240-bar ≤ +0.6%), no chasing more than 0.9% above EMA(30),
expanding hourly volatility, and a fresh regime — with a dip-recovery
bounce mode for down days. Risk-based sizing (0.45% of equity to a hard
stop), trailing stop, failed-breakout / trend-break / time-stop exits,
cooldowns, a loss-streak brake, and an equity drawdown guard. One
position at a time; deterministic, standard library only.

## Verified dev grade

Graded on this race's dev window (2026-11-22 05:11 → 2026-11-23 05:11 UTC)
with the wire-protocol driver (`python3 -P -s`, `PYTHONHASHSEED=0`, 1440
batches, two runs byte-identical):

- **Net PnL: +0.307320 USDC** (gate: > 0 — passed)
- **Trades: 2** (1 round trip: buy bar 191 @ $59,636.67, sell bar 213 @ $59,719.08)
- **Max drawdown: 0.461%** (gate: ≤ 25% — passed)
- **Fees: 0.4236 USDC**

## Layout

- `main.py` — the bot (entrypoint)
- `vendor/makefun_bars.py` — byte-identical copy of the kit's Python SDK shim
- `LICENSE` — SPDX-first license
