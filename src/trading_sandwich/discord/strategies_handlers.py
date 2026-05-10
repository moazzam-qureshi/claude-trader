"""Pure async handlers for Discord /strategies, /regime, /equity,
/decisions slash commands — Phase 3 plan Task 1.13.

These take string args (Discord delivers strings) plus the actor user
ID and the configured operator ID where the command is operator-only.
They format markdown replies for Discord (≤2000 chars, callers truncate
to 1900).

For commands that mutate state (pause, resume, regime override): they
call the same MCP tools the Portfolio Strategist uses, with one
exception — operator regime overrides are stamped triggered_by=
'operator_override' instead of 'claude_override' so the audit trail
distinguishes operator interventions from Claude's own.

Author and command-tree wiring live in discord/listener.py via
_register_strategies_commands.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.mcp.tools import strategies_command, strategies_read
from trading_sandwich.strategies.base import Regime


def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


def _prompt_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd="/app", text=True,
        ).strip()
    except Exception:
        return "unknown"


# --- /strategies list ---------------------------------------------------


async def handle_strategies_list() -> str:
    rows = await strategies_read.list_strategies(active_only=True)
    if not rows:
        return "no active or paused strategies (0 strategies)"
    lines = [f"**{len(rows)} strategies running**:", ""]
    for r in rows:
        lines.append(
            f"`{r['id']}` `{r['strategy_type']}` on `{r['symbol']}` "
            f"— {r['status']} · ${r['capital_allocated_usd']} allocated"
        )
    return "\n".join(lines)


# --- /strategies pause / resume ---------------------------------------


async def handle_strategies_pause(*, strategy_id: int, reason: str) -> str:
    result = await strategies_command.pause_strategy(
        strategy_id=strategy_id, reason=reason,
    )
    if result["status"] != "ok":
        return (
            f":x: cannot pause strategy `{strategy_id}` — "
            f"`{result.get('error', 'unknown')}`: "
            f"{result.get('message', '')}"
        )
    return f":pause_button: strategy `{strategy_id}` paused. reason: {reason}"


async def handle_strategies_resume(*, strategy_id: int, rationale: str) -> str:
    result = await strategies_command.resume_strategy(
        strategy_id=strategy_id, rationale=rationale,
    )
    if result["status"] != "ok":
        return (
            f":x: cannot resume strategy `{strategy_id}` — "
            f"`{result.get('error', 'unknown')}`: "
            f"{result.get('message', '')}"
        )
    return (
        f":arrow_forward: strategy `{strategy_id}` resumed (active). "
        f"rationale: {rationale}"
    )


# --- /regime override (operator-only) ---------------------------------


async def handle_regime_override(
    *,
    actor_id: str,
    operator_id: str,
    symbol: str,
    regime: str,
    duration_hours: int,
    rationale: str,
) -> str:
    """Operator-driven regime override. Distinct from Claude's MCP path
    in that it stamps triggered_by='operator_override'.

    Non-operator → audit row written with rejection_reason='not_operator',
    no pivot insertion."""
    legal_regimes = {r.value for r in Regime}
    if regime not in legal_regimes:
        return (
            f":x: unknown regime `{regime}`. "
            f"Valid: {sorted(legal_regimes)}"
        )

    if actor_id != operator_id:
        # Audit and refuse. Mirrors the /safety not-operator pattern.
        engine = _engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO portfolio_decisions "
                        "(decision_type, target_symbol, rationale, "
                        " market_context, decided_by, prompt_version) "
                        "VALUES ('override', :s, :r, "
                        "        CAST(:mc AS jsonb), 'operator', :pv)"
                    ),
                    {
                        "s": symbol, "r": rationale,
                        "mc": json.dumps({
                            "rejection_reason": "not_operator",
                            "actor_id": actor_id, "regime": regime,
                            "duration_hours": duration_hours,
                        }),
                        "pv": _prompt_version(),
                    },
                )
        finally:
            await engine.dispose()
        return ":x: not_operator — only the configured operator may /regime override"

    # Operator path. Read prior pivot, write new pivot stamped
    # triggered_by='operator_override'.
    engine = _engine()
    try:
        async with engine.begin() as conn:
            r = await conn.execute(
                text(
                    "SELECT to_regime FROM regime_pivots "
                    "WHERE symbol = :s ORDER BY id DESC LIMIT 1"
                ),
                {"s": symbol},
            )
            row = r.first()
            prior = row[0] if row is not None else None

            await conn.execute(
                text(
                    "INSERT INTO regime_pivots "
                    "(symbol, from_regime, to_regime, triggered_by, "
                    " triggered_at, actions_taken, prompt_version) "
                    "VALUES (:s, :fr, :to, 'operator_override', NOW(), "
                    "        CAST(:at AS jsonb), :pv)"
                ),
                {
                    "s": symbol, "fr": prior, "to": regime,
                    "at": json.dumps({
                        "duration_hours": duration_hours,
                        "rationale": rationale,
                    }),
                    "pv": _prompt_version(),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO portfolio_decisions "
                    "(decision_type, target_symbol, rationale, "
                    " market_context, decided_by, prompt_version) "
                    "VALUES ('override', :s, :r, "
                    "        CAST(:mc AS jsonb), 'operator', :pv)"
                ),
                {
                    "s": symbol, "r": rationale,
                    "mc": json.dumps({
                        "regime": regime,
                        "duration_hours": duration_hours,
                        "from_regime": prior,
                    }),
                    "pv": _prompt_version(),
                },
            )
    finally:
        await engine.dispose()

    return (
        f":dart: regime override applied to `{symbol}`: "
        f"`{prior}` → `{regime}` for {duration_hours}h. "
        f"rationale: {rationale}"
    )


# --- /equity -------------------------------------------------------------


async def handle_equity() -> str:
    snap = await strategies_read.get_account_allocation()
    total = snap["total_allocated_usd"]
    by_sym = snap["by_symbol"]
    if not by_sym:
        return f":moneybag: total allocated: $`{total}` (0 strategies)"
    lines = [f":moneybag: **total allocated:** $`{total}`", "", "by symbol:"]
    for row in by_sym:
        lines.append(
            f"  · `{row['symbol']}` ${row['allocated_usd']} "
            f"({row['strategy_count']} strats)"
        )
    return "\n".join(lines)


# --- /decisions last <duration> -----------------------------------------


def _parse_duration(s: str) -> timedelta:
    """'24h' / '7d' / '4w' → timedelta. Default 24h on parse failure."""
    s = s.strip().lower()
    if s.endswith("h"):
        return timedelta(hours=int(s[:-1]))
    if s.endswith("d"):
        return timedelta(days=int(s[:-1]))
    if s.endswith("w"):
        return timedelta(weeks=int(s[:-1]))
    return timedelta(hours=24)


async def handle_decisions_last(*, duration: str = "24h", limit: int = 20) -> str:
    cutoff = datetime.now(timezone.utc) - _parse_duration(duration)
    engine = _engine()
    try:
        async with engine.connect() as conn:
            r = await conn.execute(
                text(
                    "SELECT decided_at, decision_type, target_strategy_id, "
                    "       target_symbol, rationale, decided_by "
                    "FROM portfolio_decisions "
                    "WHERE decided_at >= :c "
                    "ORDER BY decided_at DESC LIMIT :n"
                ),
                {"c": cutoff, "n": limit},
            )
            rows = list(r)
    finally:
        await engine.dispose()
    if not rows:
        return f":scroll: no decisions in last {duration} (0)"
    lines = [f":scroll: **decisions in last {duration}** ({len(rows)}):", ""]
    for row in rows:
        when = row[0].strftime("%m-%d %H:%MZ")
        target = ""
        if row[2] is not None:
            target = f" sid:{row[2]}"
        if row[3] is not None:
            target += f" {row[3]}"
        lines.append(
            f"`{when}` **{row[1]}**{target} "
            f"(by {row[5]}) — {row[4]}"
        )
    return "\n".join(lines)
