# Amendment — DB-Backed Config + Claude Self-Tune

> **Status:** Approved by operator 2026-05-10. Supersedes the locked decision in
> `2026-05-09-phase-3-strategy-pivot-design.md` §1.4 row "git-versioned prompts"
> and the architecture.md §8 rule "Version prompts via git. No DB-mirrored prompt
> table."
> **Scope:** Phase 3 onward. Phase 0/1/2 audit data already on disk is unaffected.
> **Author:** Operator + Claude, 2026-05-10 session.

---

## 1. What this amendment changes

The original architecture made config a git-only concern: every tunable value lived
in `policy.yaml`, every change was a git commit, every Claude decision row recorded
`prompt_version = git rev-parse HEAD`, and that hash was sufficient to fully
reproduce the agent's view of the world at decision time.

This amendment moves all *runtime-tunable* parameters out of `policy.yaml` and into
a database table (`policy_settings`). `policy.yaml` is repurposed as a **seed file**
that is read once on first startup (or `cli settings reseed`) and applied to the DB.
After bootstrap, every read goes through the DB; every write (whether by operator
or Claude) leaves an audit row.

The audit guarantee — "every Claude decision is fully reproducible from disk
state" — is preserved by **snapshotting the full effective settings dict into
each decision row's new `policy_snapshot JSONB` column**, replacing the implicit
`prompt_version → git checkout → re-read policy.yaml` reproduction chain.

## 2. Why operator chose this

- Discord-driven mid-session retuning of strategy thresholds, regime cutoffs, and
  capital limits without git commits.
- Claude as portfolio strategist gets first-class authority to tune the parameters
  of the strategies it commands, in addition to deploying/pausing them.
- Per-instance strategy state (e.g., grid-btc-1's range) was already going to live
  in `strategies.params JSONB` (Task 1.2 done) — DB-backed config extends that
  pattern uniformly to the policy-wide defaults too.

## 3. What this amendment does NOT change

- **`runtime/CLAUDE.md` (the agent brain) remains git-tracked.** Persona, decision
  rubric, output format — these are prompt content, not config. `prompt_version`
  column still records `git rev-parse HEAD` at decision time. The amendment only
  moves *numeric/structural config* to DB, not the agent's instructions to itself.
- **Halal and execution-rail safety values stay on the file-only path.** See §6.
- **`strategies.params` JSONB on individual strategy instances stays as-is.** That
  was already DB-tunable per Task 1.2.
- **Migration files (`migrations/versions/*.py`) stay git-tracked.** Schema is code.
- **Spec/plan documents stay git-tracked.** Decisions are docs.

## 4. Three-tier mutability model

Operator clarification 2026-05-10 (after the original §4 was written): there are
three classes of keys with different mutability rules.

### 4.1 Tier 1 — Hard inviolable (halal/religious)

NEVER mutable through any path. No DB row. No Discord override. No MCP tool.
Reading goes through `_halal.py` (file-only, no DB code path):

```
max_leverage                              # must equal 1
longs_only                                # must equal True
universe.tiers.excluded.*                 # locked excluded symbols
universe.hard_limits.excluded_symbols_locked
```

Any attempt to mutate one of these from any path → `HalalViolationError`,
`policy_changes` row with `applied=false, rejection_reason='halal_inviolable'`,
loud Discord alert.

### 4.2 Tier 2 — Operator-safety (file seed, operator-only DB override)

File-only seed, but the operator can override at runtime via a dedicated Discord
slash command that writes directly through the settings repo (no Claude in the
loop). Claude's `set_setting` MCP tool returns `error: operator_only_key` for
these and writes a rejected `policy_changes` row.

```
max_account_drawdown_pct       # account drawdown circuit breaker
max_daily_realized_loss_usd    # daily loss circuit breaker
trading_enabled                # operator kill switch
auto_flatten_on_kill           # kill-switch behavior
```

Operator path: `/safety set <key> <value> <rationale>` in Discord. The Discord
listener calls `settings_repo.set(key, value, rationale, changed_by='operator',
authority='operator_safety')`. Repo verifies key is in Tier 2 set and authority
matches; writes `policy_settings` row, writes `policy_changes` audit row, fires
Discord notification. No Claude invocation.

### 4.3 Tier 3 — Self-tunable

All other keys. Claude and operator can both mutate via the standard `set_setting`
MCP tool or `/settings set` Discord command. Every mutation logs to
`policy_changes` + fires Discord notification — non-negotiable per operator
exchange.

### 4.4 Why the split

Operator decision 2026-05-10 chose "full self-tune, no approval" for Tier 3 and
accepted the LLM-drift failure mode (§13). But circuit breakers are precisely
the values whose drift would be catastrophic — Claude raising its own drawdown
cap defeats the safety net's purpose. So Tier 2 separates "Claude should never
raise its own ceiling" from "operator needs a fast Discord path when the ceiling
is wrong" by giving the latter a Discord-direct codepath that bypasses the LLM
entirely.

## 5. Schema additions

### 5.1 `policy_settings`

```sql
CREATE TABLE policy_settings (
    key TEXT PRIMARY KEY,            -- dotted path, e.g. 'regime_classifier.adx_trend_threshold'
    value JSONB NOT NULL,            -- typed value (int/float/string/array/object)
    value_type TEXT NOT NULL,        -- 'int' | 'float' | 'string' | 'bool' | 'array' | 'object'
    description TEXT,                -- human-readable purpose
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT NOT NULL         -- 'seed' | 'claude' | 'operator' | 'system'
);
```

### 5.2 `policy_changes`

Append-only audit log. Every successful or attempted write lands here.

```sql
CREATE TABLE policy_changes (
    id BIGSERIAL PRIMARY KEY,
    key TEXT NOT NULL,
    old_value JSONB,
    new_value JSONB NOT NULL,
    rationale TEXT NOT NULL,         -- caller MUST provide
    changed_by TEXT NOT NULL,        -- 'claude' | 'operator' | 'seed' | 'system'
    applied BOOLEAN NOT NULL,        -- false if rejected (e.g. inviolable key)
    rejection_reason TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prompt_version TEXT              -- git HEAD when claude/auto changed it
);
CREATE INDEX ix_policy_changes_key_at ON policy_changes(key, changed_at DESC);
CREATE INDEX ix_policy_changes_at ON policy_changes(changed_at DESC);
```

### 5.3 `policy_snapshot` column on decision tables

```sql
ALTER TABLE claude_decisions
    ADD COLUMN policy_snapshot JSONB;     -- nullable for pre-amendment rows
ALTER TABLE portfolio_decisions
    ADD COLUMN policy_snapshot JSONB;     -- nullable for pre-amendment rows
```

The snapshot captures the full `policy_settings` table state at decision time
plus the file-only inviolable values, merged into one dict. ~5KB per row at
expected key counts. Indefinitely reproducible: given a decision row, you can
exactly recreate what Claude was looking at.

## 6. Read path

```
load_setting(key) -> Any
  if key in TIER1_HALAL_KEYS:
      return _halal.read(key)               # file-only, no DB
  if key in TIER2_SAFETY_KEYS:
      return policy_settings_repo.get(key) or _safety_seed.read(key)
  return policy_settings_repo.get(key) or _seed_default(key)
```

Tier 2 keys are DB-readable like Tier 3 (so Discord overrides take effect);
they only differ from Tier 3 on the **write path**. Reading sees the override.

`_seed_default(key)` reads `policy.yaml` and returns the default if the DB row
is missing — covers the bootstrap case where a new key was added to the seed file
but DB hasn't been re-seeded yet. This is also how new keys roll out: ship the
key in `policy.yaml`, callers see the default immediately, DB row gets written
on first explicit set or next `cli settings reseed`.

## 7. Write path

```
set_setting(key, new_value, rationale, changed_by, authority) -> SetResult
  # authority is one of: 'mcp_default' | 'operator_safety' | 'seed'
  if key in TIER1_HALAL_KEYS:
      log to policy_changes with applied=false, rejection_reason='halal_inviolable'
      raise HalalViolationError
  if key in TIER2_SAFETY_KEYS and authority != 'operator_safety':
      # Claude (or anything with default authority) cannot mutate Tier 2.
      log to policy_changes with applied=false, rejection_reason='operator_only_key'
      raise OperatorOnlyKeyError
  validate new_value type matches stored value_type
  begin tx
    old = policy_settings.get(key)
    upsert policy_settings(key, value=new_value, updated_at=now(), updated_by=changed_by)
    insert policy_changes(key, old_value=old.value, new_value, rationale, changed_by,
                          applied=true, prompt_version=git_head_if_claude())
  commit
  notify_discord(f"settings change: {key} {old} -> {new_value} (by {changed_by}: {rationale})")
  return SetResult(applied=true, old_value=old)
```

The MCP tool always passes `authority='mcp_default'`. Only the Discord listener's
`/safety` handler passes `authority='operator_safety'`. There is no other way to
obtain operator_safety authority — the constant is defined inside the listener
module and not exported.

Discord notification fires on **every** successful change, regardless of
`changed_by`. This is the operator's only safety net for spotting drift —
non-negotiable.

## 8. MCP tool surface (Claude-callable)

```python
get_setting(key: str) -> dict           # value + value_type + description + updated_at + updated_by
list_settings(prefix: str = "") -> list  # all keys (or under a prefix)
set_setting(key: str, value: Any, rationale: str) -> dict
get_setting_history(key: str, limit: int = 20) -> list
```

`set_setting` is callable by Claude with no approval gate. Every call leaves a
`policy_changes` row with `changed_by='claude'`, `prompt_version=git HEAD`,
operator-supplied `rationale` (Claude must populate from its own reasoning).

## 9. Operator surface (Discord)

### 9.1 `/settings ...` — Tier 3 (and read-only for Tier 2)

- `/settings list [prefix]` — show current values
- `/settings get <key>` — show one value with history
- `/settings set <key> <value> <rationale>` — Tier 3 only. Calls
  `set_setting(..., authority='mcp_default', changed_by='operator')`.
  Returns `error: operator_only_key` if you try this on a Tier 2 key — it
  redirects you to `/safety set`.
- `/settings diff <since>` — recent changes since a duration/timestamp
- `/settings revert <change_id>` — restore old_value from a `policy_changes` row;
  authority is inferred from the original change's tier.

### 9.2 `/safety ...` — Tier 2, operator-only

Separate slash command surface, separate Discord listener handler. Calls
`set_setting(..., authority='operator_safety', changed_by='operator')`.

- `/safety list` — show all Tier 2 keys with current values + file-seed values
- `/safety set <key> <value> <rationale>` — operator-only Tier 2 mutation
- `/safety reset <key>` — restore the file-seed value (deletes the DB override row)

The handler verifies the requesting user is the configured operator (Discord
user ID match). Anyone else gets `error: not_operator` and an audit row in
`policy_changes` with `applied=false, rejection_reason='not_operator'`.

Tier 2 keys can NEVER be mutated through `/settings set` even by the operator —
that surface is Tier-3-only. The split is structural, not just a permission
check, so operator muscle memory ("I'll just use settings set") cannot
accidentally mutate a circuit breaker.

## 10. Snapshot generation

`snapshot_policy() -> dict` runs once at the start of every Claude shift /
portfolio-strategist invocation. Composition:

```python
{
    "settings": {row.key: row.value for row in policy_settings.all()},
    "inviolable": _safety.read_all(),     # the file-only values
    "snapshot_at": now(),
    "git_head": git_rev_parse_head(),
}
```

Persisted into `claude_decisions.policy_snapshot` (existing flow) or
`portfolio_decisions.policy_snapshot` (new portfolio-strategist flow).

## 11. Migration plan for existing callers

Phase 2.7 callers using `_policy.load_policy()` and `_policy.is_trading_enabled()`
keep working — backward compatible:

- `is_trading_enabled` — value lives in inviolable set, file-only path. No change.
- `load_policy()` — keeps reading `policy.yaml` for now, BUT new code paths
  should use `load_setting(key)`. Deprecate gradually; do not break Phase 2.7
  triage worker mid-flight.
- New Phase 3 code (Strategy Engine, regime classifier, performance tracker, all
  Wave 1+ strategies) uses `load_setting()` exclusively.

## 12. Bootstrap & reseed

On first startup after migration 0016 lands:
1. `cli settings bootstrap` runs (or auto-runs on doctor if `policy_settings` is
   empty).
2. Reads `policy.yaml`, walks every non-inviolable key, inserts a row.
3. Logs one `policy_changes` row per key with `changed_by='seed'`,
   `rationale='initial bootstrap from policy.yaml'`.

`cli settings reseed --key <key>` re-applies the YAML default for one key
(useful if Claude's tuning went wrong and you want the file default back without
digging through `policy_changes`).

## 13. Risk acceptance (operator's call, recorded for posterity)

Operator was told 2026-05-10:

> "Letting Claude tune its own thresholds introduces a new failure mode the
> previous system literally couldn't have... Each step looks locally reasonable
> in `portfolio_decisions` rationale; the cumulative drift is invisible until
> equity is gone."

Operator chose to proceed anyway. The Discord notification on every change is
the safety net. If drift is observed, mitigations available:
- Move more keys into the inviolable set (file-only).
- Add a circuit breaker: if any key has been changed >N times in M hours, pause
  Claude self-tune.
- Switch to operator-approval for a subset of keys.

These mitigations are deferred until evidence of drift exists.

## 14. Plan tasks (replaces nothing; inserted before Phase 3 plan Task 1.5)

| Task | What |
|---|---|
| AM-1 | Write this amendment + update architecture.md + update project CLAUDE.md |
| AM-2 | Migration 0016: `policy_settings` + `policy_changes` |
| AM-3 | Migration 0017: `policy_snapshot JSONB` on `claude_decisions` + `portfolio_decisions` |
| AM-4 | `src/trading_sandwich/settings/{repo.py, _safety.py, snapshot.py, seed.py}` |
| AM-5 | MCP tools: `get_setting`, `list_settings`, `set_setting`, `get_setting_history` |
| AM-6 | Discord: `/settings list/get/set/diff/revert` + auto-notify on every set |
| AM-7 | `cli settings bootstrap/reseed/list/get/diff` + doctor checks DB seeded |

Task numbering on the Phase 3 plan resumes at 1.5 after AM-7.

## 15. Failure modes & tests (must cover)

- Setting a Tier 1 (halal) key from any path → returns
  `HalalViolationError`, rejected `policy_changes` row with
  `rejection_reason='halal_inviolable'`, no `policy_settings` mutation,
  loud Discord alert.
- Setting a Tier 2 (safety) key via MCP `set_setting` (authority=mcp_default)
  → returns `OperatorOnlyKeyError`, rejected `policy_changes` row with
  `rejection_reason='operator_only_key'`, no `policy_settings` mutation.
- Setting a Tier 2 key via `/safety set` from a non-operator Discord user →
  returns `NotOperatorError`, rejected `policy_changes` row with
  `rejection_reason='not_operator'`, no mutation.
- Setting a Tier 2 key via `/safety set` from the operator → applied,
  `policy_changes` row with `changed_by='operator'`,
  `authority='operator_safety'`, Discord notification fires.
- `/settings set` attempted on a Tier 2 key → returns `operator_only_key`,
  message instructs user to use `/safety set`.
- Type mismatch (writing string to int key) → reject with type error,
  rejected `policy_changes` row.
- Concurrent writes to same key → last writer wins; both `policy_changes` rows
  visible in audit; old_value of second matches new_value of first.
- Snapshot taken at decision boundary; mid-shift settings changes don't poison
  the in-flight decision (read at start, snapshot at start, no re-reads).
- Reseed of a key not present in policy.yaml → returns `error: no_default`.
- `policy.yaml` deleted → bootstrap fails loudly, doctor red.
- DB unreachable → `load_setting` falls back to `policy.yaml` default for Tier
  3, file-seed for Tier 2, halal file path for Tier 1 (graceful degradation;
  logs a warning).

---

*End of amendment.*
