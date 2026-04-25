# Discord webhooks for the trading sandwich

The system writes to Discord through webhook URLs configured via env vars.

## Webhooks in use

| Env var | Channel purpose | Cadence |
|---|---|---|
| `DISCORD_UNIVERSE_WEBHOOK_URL` | Universe-event feed: every `add` / `promote` / `demote` / `remove` / `exclude` / `hard_limit_blocked` from the heartbeat trader | Spiky — silent for hours, then a few in a row |

The existing `DISCORD_BOT_TOKEN` (Phase 2 stage 1b) handles the trade-proposal cards on a separate channel. That's a bot token, not a webhook URL.

## Creating a webhook

1. In Discord: server settings → channel → Integrations → Webhooks → New webhook.
2. Copy the URL.
3. Add to `.env` next to the `DISCORD_UNIVERSE_WEBHOOK_URL=` line.
4. Restart `triage-worker` and `mcp-server`:
   ```
   docker compose restart triage-worker mcp-server
   ```

## Rotation

If a webhook URL is leaked or compromised:
1. Discord → integrations → delete the webhook.
2. Create a new one in the same channel.
3. Update `.env`.
4. Restart services.

## What the cards look like

**Universe mutation:**
```
🔄 Universe change — 2026-04-26 14:32 UTC
**SUIUSDT → observation (add)**

Rationale: Spotted in TradingView 24h gainers (+18%, vol $340M).
Passes Layer 1 + Layer 2 fit check. No archetype history yet —
adding to observation only, no size, will watch for 14 days.
Reversion: remove if no archetype signals fire in 21 days.

shift_id: 4721 · diary: runtime/diary/2026-04-26.md
```

**Hard-limit blocked:**
```
⛔ Hard limit blocked — 2026-04-26 14:32 UTC
Claude attempted: **promote SOLUSDT** watchlist → core
Blocked by: `core_promotions_operator_only`

Rationale: the data warrants it now
```

The blocked variant is an **important signal** — it means the hard limits
are doing real work. Read these carefully; if Claude is hitting a limit
repeatedly, that's evidence either the limit is too tight or Claude's
judgment has drifted.

## Retry semantics

If Discord is unreachable when an event fires, the universe_events row is
written with `discord_posted = false`. A Celery Beat task
(`discord_universe_retry`) runs every 15 minutes and retries unposted
events. The mutation itself is **not** rolled back — the change happened;
the operator notification is best-effort. Source of truth is `policy.yaml`
+ `universe_events`; Discord is replayable.
