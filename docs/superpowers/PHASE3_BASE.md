# Phase 3 — Strategy Pivot Base Reference

**Branch:** `phase-3-strategy-pivot`
**Base commit:** `b7cf4d288599fd46e3952d7527e0b90da2691d4f`
**Base subject:** `feat(decision-loop): chart-first decision making — break out of 91-shift paralysis`
**Branched on:** 2026-05-10
**Branched from:** `main`

## Source-of-truth documents on this branch

- Spec: [`docs/superpowers/specs/2026-05-09-phase-3-strategy-pivot-design.md`](specs/2026-05-09-phase-3-strategy-pivot-design.md)
- Plan: [`docs/superpowers/plans/2026-05-09-phase-3-strategy-pivot.md`](plans/2026-05-09-phase-3-strategy-pivot.md)
- Project policy: [`/CLAUDE.md`](../../CLAUDE.md)
- Architecture pattern: [`/architecture.md`](../../architecture.md)

## Operator confirmations captured at session start (2026-05-10)

| Item | Value |
|---|---|
| Live capital | ~$183 USDT (started session at ~$90, operator topped up +$88 mid-session 2026-05-10). Existing `policy.yaml` risk caps (calibrated for $167) remain ~appropriate; no re-derivation needed. |
| Universe | Spec §6.1 verbatim — accept as-is |
| Discord channel | Reuse `DISCORD_UNIVERSE_WEBHOOK_URL` for all Phase 3 notifications |
| CFGI feed (Wave 2) | Pre-approved (alternative.me free tier) |
| On-chain feeds (Wave 3) | Free-tier only, graceful degradation |

## Pre-flight surfaces flagged (not auto-fixed)

- `cli` compose service entrypoint (`myapp`) is broken — use `tools` service for CLI invocations.
- Binance API key not authorized from local Docker IP — live equity introspection limited to VPS.
- Untracked `scripts/probe_*.sh` and `.claude/` are pre-existing; left alone.
- `policy.yaml` risk caps (`max_daily_realized_loss_usd: 35`, `max_correlated_usd: 200`) miscalibrated against ~$90 equity; re-derive when applying Task 1.5 universe block.
