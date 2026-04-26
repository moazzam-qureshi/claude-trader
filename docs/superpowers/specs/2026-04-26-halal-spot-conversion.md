# Spec A.5 — Halal Spot Conversion

> **Status:** Spec written 2026-04-26; awaiting operator review before plan.
> **Author:** Claude
> **Predecessor:** `2026-04-26-heartbeat-trader-design.md` (Spec A — heartbeat trader)
> **Driver:** Operator religious constraint — margin trading is haram (riba/borrowing with interest). The system was originally built for spot-margin (3x max leverage); this spec converts it to halal spot (1x only, longs-only, no borrowing).
> **Base commit:** `dbf4ff5` (system safely flipped to paper + max_leverage 1)

---

## 1. Goal

Convert the trading system from **spot-margin (with leverage, with shorts)** to **halal spot (no leverage, longs-only, no borrowing)**, without disturbing the heartbeat-trader mechanic shipped in Spec A.

After this ships:
- The system queries Binance Spot wallet (not Margin)
- Order placement uses spot endpoints (not margin endpoints)
- Short positions are **rejected at the adapter layer**, not relying on Claude's discipline alone
- All persona/policy files are rewritten to reflect halal constraints
- `max_leverage: 1` is the only permitted value
- Operator can flip `execution_mode: live` and trade their actual $167 USDT spot balance

## 2. Non-goals

- **No change to the heartbeat trader mechanic.** SOUL/STATE/diary/universe-tiers/MCP-tools-shape all stay the same.
- **No change to the signal pipeline.** Same archetypes fire (the *short* archetypes will just always be filtered out before they reach a `propose_trade`).
- **No new MCP tools.** Existing 17 tools all still apply.
- **No re-spec of universe.** BTC/ETH core, SOL/BNB watchlist, LINK/ARB observation, SHIB/PEPE excluded — all unchanged.
- **No deletion of the spot-margin code.** `CCXTProAdapter` stays in the repo (in case the constraint changes in some future jurisdiction or for audit). It's just no longer the live adapter.
- **Out of scope: re-checking whether crypto itself is halal.** That's an operator decision, not a code question.

## 3. What changes (file-by-file)

### 3.1 New: `src/trading_sandwich/execution/adapters/ccxt_spot.py`

A `CCXTSpotAdapter` that mirrors `CCXTProAdapter`'s interface (so `execution-worker`'s adapter-loading logic doesn't change shape), but:

- Initializes `ccxt.pro.binance` with `defaultType='spot'` (not margin)
- `get_account_state()`: queries `fetch_balance({'type': 'spot'})`, returns USDT free as `equity_usd` and `free_margin_usd` (the latter being a misnomer for spot but kept to match the interface; "free buying power" semantically)
- `get_open_positions()`: spot doesn't have "positions" in the futures sense — instead returns synthetic position records based on non-USDT spot balances *that we opened* (tracked via `positions` table, not derived from balance)
- `get_open_orders()`: queries spot open orders
- `submit_order(side, ...)`: **rejects `side='short'` immediately with a clear error before any Binance API call**. Routes longs to spot order placement.
- `cancel_order()`: spot cancel.

**Key safety property:** even if upstream policy rails or Claude's prompt fails to filter shorts, the adapter is a hard backstop.

### 3.2 New: rail in `src/trading_sandwich/execution/policy_rails.py` — `reject_short_orders`

Adding rail #17: any proposal with `side='short'` is rejected before submission with reason `"halal_spot_no_shorts"`. Belt-and-suspenders with the adapter's check; rail catches it in the audit trail layer.

### 3.3 Modified: execution-worker adapter loader

Wherever `execution_mode: live` currently selects `CCXTProAdapter`, it now selects `CCXTSpotAdapter`. (`CCXTProAdapter` stays importable for reverting/auditing but isn't routed.)

### 3.4 Modified: `runtime/CLAUDE.md`

Substantial rewrite of three sections:

- **§1 Identity** — was "veteran spot-margin trader, 3x max leverage, BTC + ETH, can short." Becomes "halal spot trader, 1x only, no shorts, no borrowing, BTC + ETH initially." The "compounding" framing stays, the discipline framing stays, the "you are not a chat assistant" framing stays.
- **§2.4 Borrow interest** — delete the entire section. Spot has no borrow.
- **§2.5 Liquidation distance is the *real* stop** — replace with "Position sizing is the real stop." The new constraint: position size ≤ `max_order_usd`; loss is bounded by position size; you don't have margin liquidation but you can still lose 100% of a position on a hard adverse move.
- **§3 Per-regime playbooks** — `trend_down` and `trend_up`/`range × normal` short setups all become `OBSERVE` (cannot short). The cell-by-cell summary table updates: ~6 of 10 regime cells now have "wait" or "ignore" defaults *and* in the others, only longs are tradeable.
- **§4 Per-archetype notes** — short variants of each archetype (`trend_pullback short`, `divergence_rsi short`, `liquidity_sweep_*` shorts) get a clear "**not tradeable on halal spot — observe only, do not propose**" callout. Long variants are unchanged.
- **§5 Hard rules** — rule #5 (never call Binance order-placement tools) stays. New rule: **rule #5b — never propose `side='short'`. The adapter rejects it; rejection is a procedural failure on your part.** Add explicit "no margin, no leverage, no borrow" rule.

### 3.5 Modified: `runtime/SOUL.md`

Smaller change. Add a section *"On halal trading"*:

> I trade halal spot only. No margin, no leverage, no borrowing. Shorts are
> not available — I cannot sell what I do not own. This is not a constraint
> to be optimized around; it is the boundary of the work. Every trade is a
> long in something I am willing to hold with my own capital.

Update the opening identity line: "I am a discretionary crypto trader running a small, owner-operated, **halal spot** book on Binance (longs only, no leverage)."

### 3.6 Modified: `runtime/GOALS.md`

Two updates:
- "Numbers" section — drop "win rate ≥ 45% / R-multiple ≥ 1.5R" framing if it implicitly assumed leverage; restate in spot terms (gross % return per position, expected hit rate).
- "Behaviors" section — add: *"I trade only longs. Short setups are noted in the diary for learning purposes but not proposed."*

### 3.7 Modified: `policy.yaml`

Already done in commit `dbf4ff5`:
- `execution_mode: paper` (temporary safety while spec ships)
- `max_leverage: 1`

After this spec ships:
- `execution_mode: live` re-flipped (operator action)
- A new comment block at the top of `policy.yaml` documenting "halal spot account — see Spec A.5"

### 3.8 New tests

- `tests/unit/test_ccxt_spot_adapter.py` — adapter rejects shorts, formats spot orders correctly, parses spot balance correctly. **No real Binance hits in unit tests.**
- `tests/unit/test_policy_rail_no_shorts.py` — rail #17 rejects short proposals.
- `tests/integration/test_ccxt_spot_adapter_real.py` — `@pytest.mark.integration`, optionally gated by env var (skip in CI), verifies real spot adapter against testnet (fetch balance, fetch open orders, place tiny test order).

## 4. What does NOT change

| Component | Status |
|---|---|
| Heartbeat scheduler (`triage/heartbeat.py`) | unchanged |
| MCP tool surface (17 tools) | unchanged |
| Discord notifier | unchanged |
| Pydantic contracts | unchanged |
| Migrations 0011, 0012 | unchanged |
| Tiered universe in policy.yaml | unchanged |
| `policy.yaml::universe.tiers` | unchanged (BTC/ETH core etc.) |
| Signal pipeline (signal-worker, archetypes) | unchanged — short archetypes still fire to the DB; just never become trades |
| Outcome measurement | unchanged |
| `claude_decisions`, `heartbeat_shifts`, `universe_events` tables | unchanged |
| `CCXTProAdapter` (margin adapter) | kept for audit/historical, no longer routed |

## 5. Why both adapter check AND policy rail AND CLAUDE.md guidance

Defense in depth — each layer catches a different failure mode:

| Layer | Catches what |
|---|---|
| **CLAUDE.md** says "no shorts" | Claude's prompt-level discipline. Default behavior. |
| **`policy_rails.reject_short_orders`** | If Claude's discipline slips and a short proposal reaches the execution worker, the rail rejects it with audit trail before any submission. |
| **`CCXTSpotAdapter.submit_order` rejects `side='short'`** | If the rail is misconfigured or skipped (e.g., manual `propose_trade` outside the normal flow), the adapter is the final hard backstop before Binance. |

If any one layer fails alone, the others catch it. **Margin-style trades cannot reach Binance.**

## 6. Position sizing implications

The spot constraint changes risk math:

- **No leverage means $X position = $X collateral.** A $50 trade is $50 of BTC owned, not a $100 BTC position with $50 margin.
- **No liquidation means max loss per trade = position size × adverse %.** A 30% adverse move on a $50 BTC position = $15 loss.
- **Concentration risk goes up.** $50 of BTC on $167 account = 30% of equity in one position. With `max_open_positions_total: 3`, the worst case is 90% of equity in correlated long positions. Worth flagging — operator may want to lower `max_order_usd` to $25-30 to allow more diversification.
- **Borrow cost = $0.** Removes a real cost from longer-hold trades. Slightly improves the math vs. the original margin design.

Spec leaves `max_order_usd` at its current `50` value but flags it for operator review during execution.

## 7. Behavioral implications for the trader

This is the part the operator should explicitly accept:

- **In `trend_down` regimes (which is when shorts make money), the trader sits flat.** Sometimes for days. The diary will document "trend_down regime, no longs available, observing." Operator should not panic that "the system isn't doing anything" — it's working correctly.
- **Range trades become asymmetric.** `range_rejection long` at the bottom is tradeable; `range_rejection short` at the top is not. The trader becomes a "buy dips" trader, not a "fade extremes" trader.
- **Exit-only management on a position:** all stops are sells (close the long), no buy-to-cover for shorts.
- **Lower trade frequency expected.** GOALS.md says "2-8 paper trades per week." With shorts disabled, lean toward 1-4. Update GOALS during implementation.

## 8. Reversibility

If the religious constraint changes (e.g., a different scholar opinion, a different jurisdiction), reverting is:

1. Edit `policy.yaml`: `max_leverage: 1` → `2`
2. Edit execution-worker adapter loader: route `CCXTProAdapter` instead of `CCXTSpotAdapter` for `execution_mode: live`
3. Restore the deleted sections of `runtime/CLAUDE.md` from git history

The kept-but-unrouted `CCXTProAdapter` and the unchanged signal pipeline make this a one-day revert if ever needed. **But the design assumption going forward is halal spot is permanent, not provisional.**

## 9. Risks

| Risk | Mitigation |
|---|---|
| Adapter check passes but Binance somehow fills as margin | Adapter explicitly uses spot endpoints (`POST /api/v3/order`, not `POST /sapi/v1/margin/order`). API key permissions exclude margin (operator should verify this on key rotation). |
| Claude proposes a short, all three layers fail | Adapter rejection is the last gate; policy rail logs an audit event; Discord posts a `hard_limit_blocked`-style notification. Even silent failure leaves an audit trail. |
| `runtime/CLAUDE.md` rewrite drops important content | Keep a backup of the current `runtime/CLAUDE.md` (committed as `runtime/CLAUDE.spot-margin.md.bak`) for reference during the rewrite. Delete after Spec A.5 ships. |
| Operator flips back to live before conversion completes | The safety commit `dbf4ff5` set `execution_mode: paper`. Until Spec A.5 ships AND operator explicitly re-flips, the system stays paper. Discord won't fire trade orders even if rails are wrong. |
| Concentration risk on small account with no shorts | Recommend lowering `max_order_usd` to $25-30 during execution. Operator decides. |

## 10. Open questions for operator

1. **`max_order_usd`: keep 50, or drop to 25-30 for diversification?** ($25 = 15% of $167, allows ~6 concurrent positions; $50 = 30%, allows 3.)
2. **Confirm new API keys have `Spot Trading` permission and NOT `Margin Trading`?** (The constraint is at the religion layer; the API key permission is a defense-in-depth check.)
3. **`CCXTProAdapter` deletion — keep or delete after Spec A.5 ships?** Recommendation: keep for ~3 months as audit/reversibility, then delete.

## 11. Success criteria

Spec A.5 is complete when:

1. `CCXTSpotAdapter` exists and rejects `side='short'` cleanly.
2. New policy rail `reject_short_orders` exists and is registered in the rail chain.
3. `runtime/CLAUDE.md`, `SOUL.md`, `GOALS.md` are rewritten for halal spot.
4. All existing tests still pass + new tests for spot adapter + new tests for short-rejection rail pass.
5. `policy.yaml` documented at top with halal-spot constraint comment.
6. Operator can manually run `get_account_state()` against the spot adapter and see ~$167 USDT.
7. Operator flips `execution_mode: live` (one-line change), restarts execution-worker, system is genuinely live on halal spot mainnet.
8. First heartbeat shift after the flip: Claude's diary entry references "halal spot" or "longs-only" framing — confirms persona reload took effect.
9. (Soft) Within 24h post-flip, at least one paper-trade-style proposal that would be a long passes the rail, gets approved, and either fills (if conditions are right) or sits as a clean unfilled order.

## 12. Plan

Plan to follow at `docs/superpowers/plans/2026-04-26-halal-spot-conversion.md`. Tasks roughly:

1. Backup current `runtime/CLAUDE.md` to `runtime/CLAUDE.spot-margin.md.bak` (for reference during rewrite)
2. TDD: `CCXTSpotAdapter` unit tests + impl
3. TDD: `reject_short_orders` policy rail unit tests + impl
4. Rewire execution-worker adapter loader for spot
5. Rewrite `runtime/CLAUDE.md` for halal spot (largest single piece of work)
6. Update `SOUL.md` and `GOALS.md`
7. Add halal-spot comment block to `policy.yaml`
8. Manual verification: `get_account_state()` against real Binance spot, expect ~$167 USDT
9. Operator flip `execution_mode: live`, restart, watch first heartbeat
10. Watch for first halal long-only proposal in normal market hours
