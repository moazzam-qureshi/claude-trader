# Handoff — Phase 2 operational, paper-mode running

**Date:** 2026-04-25
**For:** the next Claude Code session that picks this up
**Last commit:** `2d40ad8` (triage throttle: Sonnet/low + 30min rate limit + 30/day cap)
**Branch:** `main` (operator works directly on main per Stage 1a/1b convention)
**Working tree:** clean (only `.claude/` + a few untracked `scripts/probe_*.sh` debug artifacts)

---

## Read this first, in order

1. **This file.**
2. `CLAUDE.md` (project root) — dev-session policy.
3. `runtime/CLAUDE.md` — the **trader-brain** policy that Claude reads on every triage. Has §0 *Invocation contract* (read this carefully — it defines the non-interactive contract).
4. `runtime/GOALS.md` — operator goals (currently placeholder template).
5. `policy.yaml` — runtime rails. Currently armed: `trading_enabled: true`, `execution_mode: paper`.
6. `docs/superpowers/HANDOFF-2026-04-25-phase-2-stage-1b.md` — predecessor handoff (mid-flight Stage 1b).
7. `docs/superpowers/plans/2026-04-25-phase-2-stage-1b-execution.md` — Stage 1b execution plan (T23–T44 all shipped).

---

## Current state of the world

### What's running (or can be brought up)
- **Stage 1b complete** — 24 commits, T24–T44, plus 11 follow-up commits making it actually work.
- **System is operational in paper mode.** When the stack is up, archetype signals fire → daily-cap gate selects ~3% of them → triage-worker spawns Claude with all 3 MCP servers → Claude makes data-backed decisions citing CLAUDE.md sections → decisions persist → if `paper_trade`, proposals auto-approve after 60s → PaperAdapter fills against last 5m candle.
- **Pipeline counts as of session end:** ~20,000 raw_candles, ~7,000 features, ~1,170 signals (today), 38 claude_decisions (today). All paper, no real money committed.

### Three MCP servers wired and working
- **`tsandwich`** — our system MCP, served via streamable-http at `mcp-server:8765/mcp`. 7 tools: `get_signal`, `get_market_snapshot`, `find_similar_signals`, `get_archetype_stats`, `save_decision`, `send_alert`, `propose_trade`.
- **`tradingview`** — `tradingview-mcp-server` (PyPI), stdio. ~28 tools: `coin_analysis`, `multi_timeframe_analysis`, `volume_confirmation_analysis`, `backtest_strategy`, `market_sentiment`, scans.
- **`binance`** — `binance-mcp` (npm), stdio. Read-only tools (`binanceAccountInfo`, `binanceOrderBook`, `binanceAccountSnapshot`) are allowlisted; order-placement tools are deliberately NOT allowlisted (hard rule §5 enforcement).

### Triage invocation
- Triage Celery task at `src/trading_sandwich/triage/worker.py` calls `invoke_claude` in `triage/invocation.py`.
- Spawns: `claude --model sonnet --effort low --strict-mcp-config --mcp-config /app/.mcp.json --append-system-prompt-file /app/runtime/CLAUDE.md --allowedTools <list> -p "triage <signal_id>"`.
- cwd = `/app/runtime` so the trader CLAUDE.md auto-loads.
- **Sonnet at low effort** is the deliberate choice — triage is structured tool-call → JSON, not multi-step research. Override with `TRIAGE_CLAUDE_MODEL` / `TRIAGE_CLAUDE_EFFORT` env vars if you want Opus for specific phases.

### Claude Max budget protection
Three layers, all in `policy.yaml`:
- **Per-archetype cooldown** (`per_archetype_cooldown_minutes`, 30–120min) — same archetype can't fire repeatedly.
- **Global rate limit** (`min_minutes_between_triages: 30`) — at most one triage every 30 minutes regardless of archetype/symbol/timeframe. Maps to gating_outcome `rate_limited`.
- **Daily cap** (`claude_daily_triage_cap: 30`) — hard ceiling per UTC day. Counter is in Redis (`claude_triage:<YYYY-MM-DD>`).

Realistic burn rate: ~2 triages/hour at peak × ~3% Max quota each = ~6%/hour. ~16-hour budget headroom in the worst case. **Combined with our `--model sonnet --effort low` choice this is comfortably inside Claude Max 5x.**

---

## Two real bugs found and fixed during this session (don't undo these)

### 1. MCP `__main__` double-import bug — commit `74c9d6a`
**Symptom:** `tools/list` over HTTP returned `[]` even though `mcp.list_tools()` inside the same process showed all 7 tools.
**Cause:** `python -m trading_sandwich.mcp.server` loaded `server.py` as `__main__`. Tool modules then `from trading_sandwich.mcp.server import mcp` re-imported it under the canonical name → second `FastMCP()` instance. Decorators registered on the second instance; `mcp.run()` served the first (empty) instance.
**Fix:** Routed entrypoint through `src/trading_sandwich/mcp/__main__.py`. Now `server.py` is always loaded by canonical name.
**Don't:** put `if __name__ == "__main__"` back in `server.py`.

### 2. Non-interactive permission denial — commit `c525731`
**Symptom:** Claude returned "tool denied for permissions" on every MCP call when invoked via `claude -p`.
**Cause:** Claude Code's permission model denies MCP tool calls without explicit authorization in non-interactive mode. `--permission-mode bypassPermissions` is blocked under root (which is what containers use by default).
**Fix:** `--allowedTools` flag with explicit list of every MCP tool the agent should be able to call. Order-placement Binance tools are deliberately omitted, enforcing hard rule §5 at the CLI level.
**Don't:** add Binance order-placement tools to the allowlist. The audit chain runs through `propose_trade`, not direct calls.

---

## Daily-cap counter bug worth flagging (real, not blocking)

`src/trading_sandwich/triage/daily_cap.py::check_and_reserve_slot` increments the Redis counter *before* checking the cap. So failed gate attempts still consume slots from the counter's perspective — the counter grows unbounded once the cap is exceeded.

**Symptom seen today:** counter at 527 against a cap of 50.
**Workaround:** I reset it manually via `redis-cli DEL "claude_triage:<date>"`.
**Real fix (small spec for next session):** use `WATCH/MULTI/EXEC` or check-then-INCR atomically; don't INCR on rejected attempts.
**Not a blocker** because the daily reset on UTC midnight cleans it up, and the new global rate limit (`min_minutes_between_triages: 30`) makes hitting the cap much rarer.

---

## Operating commands (memorize these)

### Bring up / down the stack
```powershell
# Start everything
docker compose up -d

# Stop everything (preserves data — postgres + redis named volumes)
docker compose stop

# Hard tear-down (loses postgres/redis state, you'd re-migrate)
docker compose down

# What's running
docker compose ps
```

### Migrations (idempotent)
```powershell
docker compose run --rm tools alembic upgrade head
```

### Watch decisions live (color-coded dashboard)
```powershell
docker compose run --rm tools python /app/scripts/watch_decisions.py
# --interval 3 for faster refresh, --once for single snapshot
```

### Quick health check
```powershell
docker compose run --rm tools python /app/scripts/daily_cap_check.py
docker compose run --rm tools python -m trading_sandwich.cli stats
```

### Pause Claude triages without stopping data ingestion
```powershell
docker compose stop signal-worker triage-worker celery-beat
# Ingestor + features keep running; no new triages fire.
# Resume:
docker compose up -d signal-worker triage-worker celery-beat
```

### Manual triage (for debugging)
```powershell
docker compose exec triage-worker bash -c "cd /app/runtime && claude --model sonnet --effort low --strict-mcp-config --mcp-config /app/.mcp.json --append-system-prompt-file /app/runtime/CLAUDE.md --allowedTools mcp__tsandwich,mcp__tradingview,mcp__binance__binanceAccountInfo,mcp__binance__binanceOrderBook,mcp__binance__binanceAccountSnapshot -p 'triage <signal_id>' < /dev/null"
```

### Kill-switch
```powershell
# Trip
docker compose run --rm cli python -m trading_sandwich.cli trading pause --reason "manual halt"

# Resume
docker compose run --rm cli python -m trading_sandwich.cli trading resume --ack-reason "cleared"

# Status
docker compose run --rm cli python -m trading_sandwich.cli trading status
```

---

## Operator decisions locked in (do not re-litigate)

These came out of the design + execution phases. Changing them needs a new spec, not an in-session pivot.

1. **Spot margin only** — Binance Isolated mode, 3x max leverage, BTC + ETH only. No perps, no Cross margin, no alts. CLAUDE.md is written for this. The live adapter (`CCXTProAdapter` at `src/trading_sandwich/execution/adapters/ccxt_live.py`) is now spot-margin not perps (Phase 2.5a, commit `f8e4b7b`).
2. **Long + short, contextually chosen** — Claude picks direction based on the data, not a hard preference. CLAUDE.md §1 principle 3 spells this out.
3. **Autonomous execution** — proposals auto-approve after `AUTO_APPROVE_AFTER_SECONDS=60` seconds. Discord card is for audit/visibility, not a gate. Operator can still reject within the window.
4. **Operator works on `main`** — no feature branches. Every commit on `main` ends in a working state.
5. **Sonnet (not Opus) for triage** — set in `invocation.py` with low effort. Override via env var if needed for specific phases.

---

## What's NOT done — known follow-up work

### Phase 2.5c — daily-cap counter fix (small, ~30 min)
The unbounded-INCR bug above. Use a CHECK-THEN-INCR pattern.

### Phase 2.6 — Grafana dashboard (~2 hours)
The `grafana/provisioning/dashboards/phase2.json` referenced in the plan was never built. The Grafana service is already in compose behind the `observability` profile. To bring it up:
```powershell
docker compose --profile observability up -d grafana
```
But you'll only see generic metrics until someone authors panels for: decisions/min, decision split, archetype × decision matrix, open proposals, kill-switch state, per-archetype calibration. The watch_decisions.py script does the same in a terminal — Grafana is a "nice to have" for dashboards on a tablet/another monitor.

### Phase 3 — outcome calibration loop (~1-2 days)
The `signal_outcomes` table is being populated (38 outcomes today). What's missing: a weekly retrospection task that joins `claude_decisions` × `signal_outcomes` on horizon=24h, computes per-archetype win-rate / median return, and writes `proposed_changes/<date>.md` for operator review. Spec exists in §10 of the original Phase 2 design doc; not implemented.

### Phase 4 — flip live (operator-driven, no code change)
Once paper-mode soak shows alert > ignore at 24h horizon (`docker compose run --rm cli python -m trading_sandwich.cli calibration`), edit `policy.yaml`: `execution_mode: paper` → `live`, commit, restart `execution-worker celery-beat triage-worker`. **Don't do this until calibration is positive AND the daily-cap bug is fixed.**

---

## What I'd do first in the next session

1. **Read this file + CLAUDE.md (project root) + runtime/CLAUDE.md.**
2. **Decide if you want the trading system running in the background** during dev work, or paused. To pause:
   ```powershell
   docker compose stop signal-worker triage-worker celery-beat
   ```
3. **Bring up only what you need for whatever you're working on.** Most dev work needs only postgres + redis. The full stack is for soak/operations, not active development.
4. **Don't touch the live mode flip** without re-reading the runbook and verifying paper-mode soak shows positive calibration (currently we have a few hours of paper data, not enough).

---

## Final state at session end

- Stack: paper mode, `trading_enabled: true`, autonomous approval enabled
- Throttle: Sonnet/low + 30min global rate limit + 30/day cap = ~2 triages/hour at peak
- Recent triage activity: 38 decisions today, all `ignore` so far (real Claude rationales citing §3.x/§4.x of trader CLAUDE.md, not fallbacks)
- Pipeline: 20k+ candles ingested, features + signals computing, decisions persisting
- Real money committed: $0
- Live mode: not armed (`execution_mode: paper`)

The system is at "operational, paper-soaking, autonomously triaging real Binance signals with Claude making policy-grounded decisions." The remaining work is observation (does paper P&L look healthy?) and the small Phase 2.5c fix before flipping live.

---

## When in doubt

- Check `git log --oneline -20` to see what shipped recently.
- Check `docker compose ps` to see service state.
- Run `python /app/scripts/watch_decisions.py --once` to see the live state.
- Re-read this handoff.
- Ask the operator before changing `policy.yaml::trading_enabled` or `execution_mode`.
