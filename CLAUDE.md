# CLAUDE.md — Development Session Policy

This file is auto-loaded by Claude Code when working on this repository.
**Audience: an agent helping a human build the Trading Sandwich system.**

This is *not* the runtime agent brain. The runtime CLAUDE.md (the policy the
live trading agent reads when triaging signals) lives at `runtime/CLAUDE.md`
and is created by Phase 0 Task 2. Do not conflate the two.

---

## What this project is

The **Trading Sandwich** — a 24/7 Python-based crypto analysis + execution
system, built as an instance of the **MCP-Sandwich** architectural pattern.

- **Architecture reference:** `architecture.md` (root). The pattern is abstract;
  this project is one instance of it.
- **Current design spec:** `docs/superpowers/specs/2026-04-21-trading-sandwich-design.md`
- **Active implementation plan:** `docs/superpowers/plans/2026-04-21-phase-0-skeleton.md`
- **Phase status:** Phase 0 (skeleton) — planning complete, implementation not
  yet started as of the most recent commit.

Read these three files before making changes.

## How work is structured

Every non-trivial change in this repo flows through three phases:

1. **Brainstorm → Spec** in `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`.
   A spec captures *what* is being built and *why*, with scope boundaries and
   success criteria. Approved by the user before implementation.
2. **Spec → Plan** in `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`. A plan
   breaks the spec into bite-sized TDD tasks: RED (failing test) → GREEN
   (minimal implementation) → commit.
3. **Plan → Execution** one task at a time, with human checkpoints.

**Never skip ahead.** Do not implement from a spec directly; write the plan.
Do not implement without a spec; write the spec. This discipline is how the
system compounds rather than accumulating one-off fixes.

Use the superpowers skills when they match (`brainstorming`, `writing-plans`,
`executing-plans`, `test-driven-development`, `systematic-debugging`). They are
rigid skills — follow them exactly, do not paraphrase.

## How to execute the Phase 0 plan

The plan file at `docs/superpowers/plans/2026-04-21-phase-0-skeleton.md`
contains a **"Handoff to Next Session"** section near the top. Read it first.

Summary: 28 tasks, TDD throughout, checkpoints at Tasks 4 / 9 / 14 / 20 / 27.
Task 28 is a human-run smoke test.

## Execution environment — Docker-only

**Do not install Python, uv, or any dependency on the host.** Everything runs
in containers. The host needs only `docker`, `docker compose`, and `git`.

Two oneshot compose services (defined in Phase 0 Task 4) are the interface:

| Host command (never run) | Use this instead |
|---|---|
| `pytest <args>` | `docker compose run --rm test <args>` |
| `alembic <args>` | `docker compose run --rm tools alembic <args>` |
| `ruff check <args>` | `docker compose run --rm tools ruff check <args>` |
| `python -m trading_sandwich.<x>` | `docker compose run --rm tools python -m trading_sandwich.<x>` |

Integration tests use `testcontainers` via a mounted Docker socket
(`/var/run/docker.sock`). On Windows Docker Desktop this requires the env vars
`TESTCONTAINERS_RYUK_DISABLED=true` and `TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal`
— both set in the test service definition.

If an integration test cannot reach its testcontainer, check the socket mount
and `host.docker.internal` resolution before assuming a code bug.

## Locked decisions (do not re-litigate)

These were decided during the design session. Changing them requires a new
spec, not an in-session pivot.

- **All-Python stack.** MCP server uses the official `mcp` Python SDK / FastMCP.
- **Five long-lived workers** fully split: ingestor, feature-worker,
  signal-worker, outcome-worker, execution-worker (execution arrives Phase 3).
- **Celery + Redis** for task queue and scheduler (Beat). Not hand-rolled queues.
- **Alembic** for every schema change. No raw SQL migrations, no untracked
  schema edits.
- **CCXT Pro** for Binance connectivity. Not a direct SDK.
- **`pandas-ta` + `TA-Lib`** for indicators. Do not hand-implement RSI/MACD/ATR.
- **Raw data is kept forever.** Never delete, never aggregate-and-drop.
- **Every decision leaves a trace** in an event log table.
- **Every prompt/policy change is a git commit.** `git rev-parse HEAD` is
  captured in the `prompt_version` column of `claude_decisions`.
- **Testcontainers** for integration test isolation — not mocks.
- **Phase 0 scope is fixed:** 2 symbols × 2 timeframes (1m, 5m), 3 indicators
  (EMA/RSI/ATR), 1 archetype (`trend_pullback`), horizons `15m` + `1h`.
  No Claude integration, no execution. Extensions belong in Phase 1+.

## Working discipline

**Default to no comments.** Code and tests should be self-explanatory. Only add
a comment when the *why* is non-obvious (a hidden constraint, subtle invariant,
workaround for a specific bug). Don't explain *what* the code does.

**Commits are small and frequent.** Each plan task ends with a commit. Do not
batch multiple tasks into one commit. Do not amend; always create a new commit.

**Commit messages follow Conventional Commits** (`feat:`, `fix:`, `chore:`,
`docs:`, `test:`, `ci:`). Look at `git log` for style; follow what's there.

**Never skip hooks** (`--no-verify`, `--no-gpg-sign`). If a hook fails, fix
the underlying issue.

**Never use destructive git commands** (`reset --hard`, `push --force`,
`branch -D`, `checkout .`) without explicit user confirmation.

**If a plan task is wrong**, fix the specific code or test. Do not restructure
the plan mid-execution; if the plan has a real defect, stop, flag it, and wait
for user direction.

## Repository conventions

- **Source:** `src/trading_sandwich/` — one package, subpackages per concern
  (`ingestor/`, `features/`, `signals/`, `outcomes/`, `execution/`, `mcp/`, `cli/`).
- **Tests:** `tests/unit/` (pure, fast) and `tests/integration/` (testcontainers-backed,
  marked with `@pytest.mark.integration`).
- **Migrations:** `migrations/versions/` — Alembic, numbered `NNNN_name.py`.
- **Docs:** `docs/superpowers/specs/` and `docs/superpowers/plans/`. Dated filenames.
- **Policy files:** `policy.yaml` at repo root for runtime thresholds/caps;
  changes are git-tracked, version recorded on every order and decision.
- **Runtime agent brain:** `runtime/CLAUDE.md` (created Phase 0 Task 2, filled
  in Phase 2). Separate from this file.
- **Proposed changes:** `proposed_changes/` — markdown files Claude writes during
  weekly retrospection for human review. Gitignored until committed manually.

## Context for the human

The user is building this as a long-running personal leverage system, not a
product for others. Single-operator by design. The goal is compounding value
over years: ingested data becomes a private dataset, CLAUDE.md refinements
accumulate into a trained operator, and the MCP tool surface becomes leverage
that's hard to replicate. Optimize for the two-year view, not the two-week one.

When in doubt about scope, prefer less. YAGNI applies hard here — the
architecture document's "defer the UI" principle extends to most things.

## Getting unstuck

If you're confused about what to do next:
1. Re-read the active plan's "Handoff to Next Session" section.
2. Check `git log --oneline` to see what's been committed.
3. Run `git status` to see what's in-flight.
4. If still unclear, ask the user rather than guessing.
