# Trading Sandwich — Goals (operator-authored, narrative)

This file is read by Claude on every triage invocation. It states *what
this trading system is trying to achieve* — distinct from `runtime/CLAUDE.md`,
which states *how to think and act*.

The contents below are **placeholders**. The operator personalizes them.
Every revision is a git commit; the SHA is recorded in
`claude_decisions.prompt_version` on every invocation.

---

## Target return and horizon

Compound USD account from $X to $Y over N months. Operator: edit this
section to your target.

## Maximum acceptable drawdown

Peak-to-trough drawdown above 10% of equity is unacceptable. The kill-switch
auto-trips at the `max_account_drawdown_pct` threshold in `policy.yaml`.

## Preferred hold durations

Prefer setups with 4h–3d expected holds. Avoid scalps shorter than 1h
unless the regime is `range × normal` and a `range_rejection` archetype
is firing with high `find_similar_signals` evidence.

## Avoided conditions

- No new positions during FOMC weeks (operator updates manually).
- Reduced size on weekends (Saturday/Sunday UTC) — set
  `first_trade_size_multiplier` lower temporarily if desired.
- No counter-trend trades (`divergence_*`, `range_rejection`) when ADX > 30.

## What success looks like

- 3-month checkpoint: at least 50 `claude_decisions` rows, calibration query
  shows `alert` median 24h return ≥ `ignore` median.
- 6-month checkpoint: positive aggregate paper P&L across all archetypes;
  per-archetype stats show realistic win-rates.
- 12-month checkpoint: live mode armed for at least 3 months without a
  reconciliation drift event or an unattended drawdown >5%.

## Non-goals

- Maximize trade count. The cap is `claude_daily_triage_cap: 20`; exceeding
  it costs nothing because gating already absorbed the noise. The system
  is engineered for selectivity, not coverage.
- Beat any benchmark. The benchmark is "operator's calendar", not
  "S&P 500" or "Bitcoin price".
- Generate alpha across all market regimes. If the system passes 80% of
  setups in the wrong regime, that is the system working correctly.

---

*Update sections as goals evolve. Every change is a `git commit`.*
