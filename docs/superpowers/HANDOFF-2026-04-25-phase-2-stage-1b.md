# Handoff — Phase 2 Stage 1b execution, mid-flight

**Date:** 2026-04-25
**For:** the next Claude Code session that continues Stage 1b execution
**Working tree clean? Yes** (only `.claude/` is untracked, which is gitignored agent-state)
**Branch:** `main` (no feature branch — operator chose to work on main)
**Last commit:** `f688345 feat: ExchangeAdapter ABC for paper and live adapters`

---

## Read this first, in this order

1. **This file** (you're here).
2. `CLAUDE.md` (project root) — the development-session policy.
3. `architecture.md` — the MCP-Sandwich pattern.
4. `docs/superpowers/specs/2026-04-25-phase-2-claude-triage-design.md` — the Phase 2 spec (updated with Stage 1a deltas).
5. `docs/superpowers/plans/2026-04-25-phase-2-stage-1b-execution.md` — **the plan you are executing**. 22 tasks, T23–T44. T23 is done.
6. `docs/superpowers/plans/2026-04-25-phase-2-stage-1a-triage-loop.md` — the predecessor plan (for stylistic reference; do not re-execute).

---

## What's done

### Stage 1a — fully shipped (commits `d51664a..70e3d63`, 22 tasks)
- 7 MCP tools: `get_signal`, `get_market_snapshot`, `find_similar_signals`, `get_archetype_stats`, `save_decision`, `send_alert`, `propose_trade`
- Migration 0010 with all Phase 2 tables (`orders`, `trade_proposals`, `order_modifications`, `positions`, `risk_events`, `kill_switch_state`, `alerts`)
- Daily-cap Redis gate wired into `signal-worker` gating (4-stage gate: threshold → cooldown → dedup → daily_cap)
- Triage Celery task that subprocess-spawns `claude -p` (`triage_signal`)
- Discord button-approval state machine (approve/reject/expire/sweep) with FOR-UPDATE locking
- Proposal sweeper (60s Celery Beat) for TTL expiry
- Approval loop E2E test (`test_approval_loop_e2e.py`)
- 201 tests pass

### Stage 1b — Task 23 done (`f688345`)
- `ExchangeAdapter` ABC at `src/trading_sandwich/execution/adapters/base.py`
- 2 unit tests pass

### Spec deltas committed (`abe12ae`)
- Workspace bind-mount path: `/app` (not `/workspace`) to match existing compose layout
- Dockerfile dep list is hardcoded (does NOT read from pyproject.toml) — must update both files when adding deps
- Stage 1a status note added to spec header

---

## What's not done — Stage 1b Tasks 24–44 (21 tasks remaining)

All tasks are written out in full TDD detail in:
`docs/superpowers/plans/2026-04-25-phase-2-stage-1b-execution.md`

**Execute them via `superpowers:executing-plans` skill** — follow the steps exactly as written. The plan was self-reviewed and the file paths, code blocks, exact commands, and expected output are all in place.

### Plan layout (continuation from T23)
- **Phase F — paper execution worker** (T24–T28): PaperAdapter, submit_order task, paper_match Beat job, paper E2E
- **Phase G — policy rails + kill-switch + watchdog** (T29–T33): 16 rails, persistent kill-switch, reconciliation watchdog, calibration helper
- **Phase H — live adapter** (T34–T35): CCXTProAdapter (manual integration only), live-mode safety smoke
- **Phase I — CLI + compose + runtime/CLAUDE.md** (T36–T44): CLI subcommands, GOALS.md, **CLAUDE.md rewrite (the big one)**, .mcp.json, 4 compose services, Prometheus targets, smoke test

### Checkpoints in the plan
- After T28 — paper E2E green
- After T33 — rails + kill-switch + watchdog
- After T35 — live adapter wired
- After T44 — Phase 2 ship-ready

---

## Operating rules to follow exactly

These were locked in during this session. Do not re-decide them.

1. **Work directly on `main`.** No feature branch, no worktree. Operator chose this.
2. **Docker-only execution.** Never install Python deps on the host. Use `docker compose run --rm test` for tests, `docker compose run --rm tools` for ad-hoc Python. Both services share the same image (built from `Dockerfile`); `tools` has `entrypoint: []`, `test` has `entrypoint: ["pytest"]`.
3. **Every plan task ends with a commit.** Conventional Commits style (`feat:`, `test:`, `docs:`, etc.). Co-author trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Use the HEREDOC pattern shown in plan tasks.
4. **No `--no-verify`, no destructive git.** If a hook fails, fix the underlying issue. If you need to reset/discard, ask first.
5. **TDD throughout.** Failing test → minimal implementation → green → commit. Don't batch. Don't skip the failing-test step.
6. **Add new deps to BOTH `pyproject.toml` AND `Dockerfile`.** The Dockerfile hardcodes its `uv pip install --system <deps>` list and does NOT read from pyproject. Forgetting this was a 30-minute rabbit hole in Stage 1a.
7. **Workspace mount: `/app`.** All bind-mounts use `./` → `/app`. The triage-worker reads `TS_WORKSPACE` env var to find its workspace; default is `/app`.
8. **Tests use `env_for_postgres` and `env_for_redis` fixtures** from `tests/conftest.py`. They wire env vars and reset module singletons (settings, engine, Celery pool). Pattern-match off `tests/integration/test_phase2_migrations.py` for migration tests, `tests/integration/test_approval_loop_e2e.py` for full-stack tests.
9. **Use `await session.flush()` between FK-dependent inserts in tests.** SQLAlchemy doesn't always order inserts correctly. Stage 1a Task 13 caught this — `signals` must flush before `claude_decisions` references it.
10. **Patch policy accessors via the `_policy` module, not the imported name.** When `gating.py` imports `from trading_sandwich._policy import get_X`, monkeypatching `trading_sandwich._policy.get_X` does NOT update gating.py's bound reference. Use `from trading_sandwich import _policy` and call `_policy.get_X()` so monkeypatch hits the live attribute. Stage 1a Task 6 caught this.

---

## Pitfalls discovered during Stage 1a (you'll likely re-encounter)

### Image rebuild required when adding deps
After editing `Dockerfile`, run `docker compose build test` (foreground, ~20–25 min on first run, faster after). The `--no-cache` flag forces a fresh install. Without rebuild, `mcp[cli]`, `anyio`, and `discord.py` were not present and tests failed with `ModuleNotFoundError`.

### `tee` to a file BUFFERS aggressively
`docker compose build 2>&1 | tee /tmp/build.log | tail -5` shows nothing useful in real time. Don't pipe through `tee`. Use `run_in_background: true` on the Bash tool and check the temp output file directly when you get the completion notification.

### Discord package name collision
The Python package `discord.py` installs as top-level module `discord`. Our internal package `trading_sandwich.discord` is fine because it's a sub-package. Both coexist; verified in Stage 1a Task 13.

### `sleep N` rejects extra positional args
The `test_invoke_claude_timeout` test originally used `CLAUDE_BIN="sleep 5"`, but `claude -p triage <id>` invocation appends `-p` and the signal id, which `sleep` parses as args. Use `python -c 'import time; time.sleep(5)'` instead. Stage 1a Task 16 caught this.

### Eager Celery + asyncio testcontainers
For tests that exercise Celery tasks, set:
```python
celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True
```
**before** the task is dispatched. The `tests/conftest.py` `_reset_module_singletons` resets these between tests. See `test_triage_task_eager.py` and `test_approval_loop_e2e.py` for the pattern.

### Background-task notifications are NOT user input
You'll see system reminders like `<task-notification>...completed</task-notification>` when background bash tasks finish. They are **not** answers from the user. Do not reply or treat them as confirmation of anything. Continue the work in progress.

---

## How to resume execution

In the new session, after reading this file and the plan:

1. **Verify state matches expected:**
   ```bash
   git -C /d/Personal/Projects/trading-mcp-sandwich log --oneline -5
   ```
   Expect the top entry to be `f688345 feat: ExchangeAdapter ABC for paper and live adapters`.

2. **Verify tests are still green:**
   ```bash
   docker compose run --rm test 2>&1 | tail -3
   ```
   Expect: `203 passed` (201 from Stage 1a + 2 from Stage 1b T23).

3. **Invoke the executing-plans skill:**
   - Use the `Skill` tool with name `superpowers:executing-plans`.
   - When the skill loads, read the plan file:
     `docs/superpowers/plans/2026-04-25-phase-2-stage-1b-execution.md`
   - Skip Tasks 1–22 (Stage 1a) and Task 23 (already done).
   - Start at **Task 24: PaperAdapter — market order fills**.

4. **Pace decision to make with the operator at the start:**
   The operator said *"Keep going / option A / straight through"* in Stage 1a. Confirm whether the same applies to Stage 1b's remaining 21 tasks, or whether they want a different cadence (option B = stop at each checkpoint, option C = one phase per session). The plan has natural checkpoints after T28, T33, T35, T44.

5. **Heads-up on expensive tasks:**
   - **T38 (`runtime/CLAUDE.md` rewrite, ~600-900 lines)**: this is content-heavy. The plan has a skeleton. Ask the operator if they want to author it interactively (give them drafts and let them refine), or if they want my best-effort first draft committed and they'll revise after.
   - **T41 (triage-worker Docker stage with Node.js + `@anthropic-ai/claude-code`)**: this is a 10–15 minute foreground rebuild. Plan accordingly.
   - **T34 (CCXTProAdapter)**: structural unit test only. Real Binance integration is operator-only.

---

## Key file locations

```
/d/Personal/Projects/trading-mcp-sandwich/
├── CLAUDE.md                               # development-session policy
├── architecture.md                          # MCP-Sandwich pattern
├── policy.yaml                             # numeric rails — DO NOT add execution_mode=live
├── Dockerfile                              # MUST add deps here AND in pyproject.toml
├── docker-compose.yml                       # add 4 new services in T40–T42
├── pyproject.toml                          # has mcp[cli], anyio, discord.py from Stage 1a
├── runtime/
│   ├── CLAUDE.md                           # Phase 0 stub — REWRITE in T38
│   └── GOALS.md                            # CREATE in T37
├── .mcp.json                               # CREATE in T39
├── src/trading_sandwich/
│   ├── _policy.py                          # has all Phase 2 accessors
│   ├── celery_app.py                       # add execution queue + beats in T25, T27, T31
│   ├── cli.py                              # add subcommands in T36
│   ├── contracts/phase2.py                 # all Phase 2 Pydantic types
│   ├── db/models_phase2.py                 # all new ORM models
│   ├── discord/                            # listener.py, approval.py, embed.py, webhook.py
│   ├── execution/                          # WHERE STAGE 1b LIVES
│   │   ├── adapters/
│   │   │   ├── base.py                    # DONE T23
│   │   │   ├── paper.py                   # T24
│   │   │   └── ccxt_live.py               # T34
│   │   ├── worker.py                      # T25
│   │   ├── policy_rails.py                # T29 (T25 ships a no-op stub)
│   │   ├── kill_switch.py                 # T30
│   │   ├── watchdog.py                    # T31
│   │   ├── paper_match.py                 # T27
│   │   ├── calibration.py                 # T33
│   │   └── proposal_sweeper.py            # already exists (Stage 1a T21)
│   ├── mcp/                                # Stage 1a — fully built
│   │   ├── server.py
│   │   └── tools/                         # 4 modules: reads, decisions, alerts, proposals
│   ├── signals/gating.py                  # has 4-stage gate including daily_cap
│   └── triage/                             # Stage 1a — fully built
│       ├── invocation.py                  # invoke_claude subprocess wrapper
│       ├── worker.py                      # triage_signal Celery task
│       └── daily_cap.py                   # Redis date-keyed counter
├── migrations/versions/
│   ├── 0001..0009                          # Phase 0 + 1
│   └── 0010_phase2_execution_and_proposals.py  # Phase 2 tables
├── tests/
│   ├── conftest.py                         # env_for_postgres, env_for_redis fixtures
│   ├── fixtures/fake_claude.py            # stub binary for triage tests
│   ├── unit/
│   └── integration/
└── docs/superpowers/
    ├── specs/2026-04-25-phase-2-claude-triage-design.md
    ├── plans/2026-04-25-phase-2-stage-1a-triage-loop.md
    ├── plans/2026-04-25-phase-2-stage-1b-execution.md   # YOU EXECUTE THIS
    └── HANDOFF-2026-04-25-phase-2-stage-1b.md           # this file
```

---

## Live-mode arming runbook (operator instructions, T44 finalizes)

**Do NOT do this for the operator. This is informational so you understand what "ship-ready" means.**

After T44 ships:
1. Operator sets `BINANCE_API_KEY`, `BINANCE_API_SECRET`, `DISCORD_BOT_TOKEN`, `DISCORD_OPERATOR_ID`, `DISCORD_WEBHOOK_URL` in `.env`.
2. `docker compose up -d` brings up 13 services with `execution_mode=paper` and `trading_enabled=false`.
3. Soak for 14 days, accumulate `claude_decisions` rows.
4. `docker compose run --rm cli calibration` — verify `alert` median 24h return ≥ `ignore` median.
5. Operator manually edits `policy.yaml`: `trading_enabled: true` AND `execution_mode: live`.
6. Operator git-commits the policy.yaml change. The commit SHA is the audit record.
7. `docker compose restart execution-worker celery-beat`.
8. First trade is size-capped at 50% by `first_trade_size_multiplier` until the first profitable close that day.

The plan does NOT auto-flip live mode. The operator decides when.

---

## When in doubt

- Check `git log --oneline -10` to confirm what shipped.
- Check `git status` to see if anything is in flight.
- Re-read this handoff.
- Re-read the plan section for the task you're on.
- Re-read `CLAUDE.md` (project root).
- If still unclear, ask the operator before guessing.
