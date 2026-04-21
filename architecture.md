# Personal Leverage System Architecture

**Pattern name:** MCP-Sandwich
**Purpose:** A reusable architecture for building personal leverage applications where Claude Code is the intelligence engine, MCP tools are the interface to your data and the world, and events (cron, CLI, webhooks) are the triggers.

This document defines the pattern abstractly. Any specific application (Upwork proposal automation, inbound lead qualifier, content idea miner, investment watch, meeting prep assistant, cold outreach drafter, job candidate tracker, etc.) is an **instance** of this pattern — differing only in data source, tool surface, and prompt content.

---

## 1. Core Insight

Traditional agent-building inverts the wrong way: developers wrap an LLM in code and call it an agent. That pattern forces you to write orchestration, retry logic, tool glue, and prompt plumbing — all work that scales poorly.

The **MCP-Sandwich** inversion:

- **Claude Code is the agent**, not something you build.
- **Your job is to build its environment**: give it high-quality data, well-scoped tools, and clear policies.
- **Trigger it at the right moments**, then get out of the way.

The outcome: your code shrinks dramatically. What remains is data plumbing (ingestion + storage), tool definitions (MCP server), policy (CLAUDE.md), and an invocation layer (CLI). Everything reasoning-shaped is delegated to Claude.

---

## 2. The Three-Layer Sandwich

```
┌─────────────────────────────────────────────────┐
│  TOP — Surfaces where you meet the work         │
│   (Discord, Telegram, Email, CLI output,        │
│    dashboard, push notifications)               │
└─────────────────────────────────────────────────┘
                      ▲
                      │
┌─────────────────────────────────────────────────┐
│  MIDDLE — Claude Code (the engine)              │
│    - reads CLAUDE.md for policy and voice       │
│    - reasons, plans, decides                    │
│    - uses MCP tools for data + action           │
└─────────────────────────────────────────────────┘
                      ▲
                      │ MCP protocol
                      │
┌─────────────────────────────────────────────────┐
│  BOTTOM — Data + World Access                   │
│    - durable store (Postgres or SQLite)         │
│    - ingestion (pulls world into store)         │
│    - custom MCP server (tools over store +      │
│      external actions)                          │
│    - external MCP servers (Composio, etc.)      │
└─────────────────────────────────────────────────┘

           Trigger: cron / webhook / CLI / /loop
```

**Three protocols connect everything, no glue code:**

1. **Database wire protocol** — the only durable state channel
2. **MCP (Model Context Protocol)** — the only Claude ↔ data/action channel
3. **HTTP** — the only path to external services

If you find yourself wanting subsystem A to "drop a file that subsystem B watches," that's glue. Kill it, route through one of the three protocols.

---

## 3. Ingredients

Every instance of the pattern has the same five ingredients. Only their contents vary.

### Ingredient 1: Durable Store

Source of truth for:
- Raw data ingested from the world (**kept forever** — the moat compounds)
- Extracted features (structured signals derived from raw, re-derivable)
- Vector embeddings (for semantic search)
- System decisions and outputs (scores, drafts, alerts)
- Outcomes (the learning substrate)
- Event log (every meaningful decision, for audit and retrospection)
- OAuth tokens that rotate (don't store rotating tokens in env files)

**Choose Postgres + pgvector** for multi-year, compounding systems where vector search matters.
**Choose SQLite** for ephemeral or truly tiny systems where portability beats features.

The single most important discipline: **keep the raw payload forever**. You don't know today what you'll want to analyze tomorrow. Disk is cheap. Retroactive data collection is impossible.

### Ingredient 2: Ingestion

A dumb, dependency-minimal process that:
- Polls or subscribes to a world data source (API, webhook, scrape, inbox)
- Writes raw payloads to the store
- Optionally extracts deterministic features into a separate table
- Runs on a timer (systemd, cron) or on external trigger (webhook)

**Ingestion never invokes Claude.** Keep it boring. LLM reasoning is a separate, event-triggered pass — not inline with ingestion.

Implement ingestion behind an **adapter interface**:

```typescript
interface DataSource {
  fetch(filter: FetchFilter): Promise<RawItem[]>;
}
```

This lets you swap `SeedDataSource` (hand-fed JSON during development), `ApiDataSource` (real production source), and mock implementations — without touching anything downstream.

### Ingredient 3: Custom MCP Server

A thin, stateless server exposing tools to Claude Code. Two categories:

**Execution tools** — read and write store data:
- `list_pending_items`, `get_item`, `save_decision`, `save_output`, `log_outcome`

**World-action tools** — effect external systems:
- `send_alert`, `create_doc`, `post_message`, `render_diagram`

**Design rules:**
- **Thin wrappers.** Each tool does one thing. No business logic; that lives in CLAUDE.md.
- **Idempotent writes.** Use natural keys so retries don't double-insert.
- **Rich reads.** `get_item(id)` returns item + features + history + similar items in one call. Claude shouldn't need round-trips.
- **Stateless.** No caches, no session. Every call is a fresh query.
- **Typed I/O.** Use schemas (Zod, JSON Schema) so Claude's tool calls stay grounded.

Also connect **external MCP servers** here:
- **Composio MCP** for Google Docs, Gmail, Slack, Notion, etc. — zero OAuth code on your side.
- Community or first-party MCP servers for specific platforms.

### Ingredient 4: `CLAUDE.md` — the Agent Brain

A single markdown file at your workspace root. Claude Code auto-loads it on every invocation. Sections:

**Role and operating modes**
- Who the agent is.
- Modes it's invoked in (e.g., `triage`, `draft`, `research`, `report`) and expected behavior per mode.

**Decision rubric**
- How to evaluate items. Factors, weights, thresholds.
- **Graceful degradation rules** — which fields are optional, what to do when they're null.

**Output spec**
- Structure, tone, length constraints for the outputs you care about (cover letters, emails, diagrams, reports).

**Voice and tone**
- Examples of how you write. What to avoid.

**Decision policies**
- Hard rules ("never output X if Y").
- Tool-usage conventions ("always call `find_similar` before finalizing a decision").

**Tool reference (optional)**
- Short note on which tools exist and when to use each. Tool descriptions themselves stay minimal; the usage convention lives here.

CLAUDE.md **is** your prompt engineering. Git-tracked. Versioned by commit hash, referenced in event logs so you know which policy produced which decision.

### Ingredient 5: CLI — the Single Invocation Surface

One binary, typed commands, disciplined invocation.

```
myapp triage                  # Claude-invoking
myapp draft <id>              # Claude-invoking
myapp ready                   # DB-direct (no Claude)
myapp submit <id>             # DB-direct
myapp outcome <id> <stage>    # DB-direct
myapp stats                   # DB-direct
myapp doctor                  # system health
myapp auth login              # bootstrap OAuth flows
```

**Two command categories:**

- **Claude-invoking** — LLM reasoning required. CLI spawns `claude -p "<mode> <args>"`, parses JSON output, logs an event row.
- **DB-direct** — pure database operations. No LLM involved. Fast, cheap, used for observability and simple mutations.

**One canonical Claude-invocation function** that:
1. Validates preconditions (workspace present, CLAUDE.md exists, DB reachable, MCP servers responding).
2. Captures prompt version (`git rev-parse HEAD`).
3. Spawns `claude -p` with `cwd=<workspace>`.
4. Parses structured JSON output.
5. Logs event row (input context + output + duration + prompt version).

Every automated trigger (cron, systemd, webhooks) funnels through this CLI. **Claude is never invoked directly — always through the CLI.** This keeps invocation discipline in one place.

---

## 4. Deployment Shape

Single `docker-compose.yml`. Five services, one network, no public ports unless you need them.

| Service | Lifecycle | Role |
|---|---|---|
| **postgres** | long-lived | Durable store |
| **core** | long-lived | MCP server + `claude` CLI + your CLI on PATH |
| **ingestor** | oneshot (systemd trigger) | Polls data source, writes to store |
| **cli** | oneshot (systemd or human trigger) | Runs your CLI commands inside the network |
| **dashboard** (optional) | long-lived | Web UI if you need one — defer until CLI friction proves it |

Volumes:
- Named volume for Postgres data
- Named volume for Claude Code OAuth token (owned by compose project, not host `~/.claude/`)
- Bind mount of the workspace directory into the containers

The `cli` container is `docker compose run --rm cli <command>` — attaches to the network, runs, exits. systemd timers on the host just invoke this. No complex host setup.

**Auth is a CLI command, not a filesystem assumption:**

```
myapp auth login       # interactive OAuth flow inside container, writes to volume
myapp auth status      # check current auth
```

This follows openclaw's pattern — portable, explicit, no host-level `~/.claude/` dependency.

---

## 5. Scheduling & Event Triggers

systemd user timers on the host trigger `docker compose run --rm cli`. Minimum useful timers:

| Timer | Purpose |
|---|---|
| **Ingest** | Pull new world data into the store |
| **Process** | Invoke Claude to reason over new data (score, draft, classify, etc.) |
| **Health** | Run `myapp doctor`, alert on failure |
| **Report** (weekly) | Intelligence/retrospection pass |

**Concurrency safety:** Use a Postgres advisory lock keyed on command name to prevent overlapping invocations. A triage still running when the next timer fires — the new one exits immediately.

**Failure handling:** Health timer sends alerts via the same external channel you use for outputs (Discord, Telegram, email). Same plumbing — no separate monitoring stack.

---

## 6. The Learning Loop

What turns a tool into a compounding asset.

### Four types of learning, ordered by ROI

**1. Outcome feedback** (ship this from day one)
- Every Claude decision (score, draft, recommendation) logs input context + output + prompt version to the event log.
- Every downstream outcome (reply, click, conversion, hire) attaches to the originating decision.
- Claude's decision tools can then query historical performance: "show me the outcome of my 10 most similar past decisions." Decisions become grounded in real data, not just current context.

**2. Preference learning** (ship once you have ~50+ outcomes)
- Capture your edits when you modify Claude's drafts before acting.
- Diff pre-edit vs. post-edit.
- Summarize diffs periodically into a "user preferences" section you fold back into CLAUDE.md.
- Claude starts sounding like **you**, not generic Claude.

**3. Prompt evolution** (ship once you have ~100+ outcomes)
- Weekly `retrospect` command: Claude reviews recent outcomes, proposes edits to CLAUDE.md or the rubric files.
- Proposed changes land in a `proposed_changes/` directory.
- You review, approve, merge. The agent improves its own environment.

**4. Cross-domain memory** (ship once you have app #2)
- Patterns that emerge across multiple personal-leverage apps: when you're productive, which channels you respond to, what hours certain decisions perform best.
- Stored in a shared memory directory ingested by all instances.

### The substrate

All four learning types require the same foundation: **every decision leaves a trace**.

Event log table with: timestamp, type, input context (JSON), output (JSON), prompt version (git hash), duration, metadata. Non-negotiable. Without it, no learning type works.

---

## 7. What Makes This Compound

**Data moat.** Three years of ingested raw data is worth more than any competitor's three days. You cannot retroactively collect what you didn't store. Instances of this pattern that run uninterrupted for years become **private datasets** no one else has.

**Prompt moat.** Your CLAUDE.md, refined over hundreds of outcome-calibrated edits, encodes your judgment in a form Claude can execute. It's not a prompt — it's a trained operator.

**Tool moat.** Your MCP server, shaped by actual use cases, becomes a leverage multiplier. Each new tool you add compounds with the existing ones — Claude can now combine them in ways you didn't anticipate.

**Connection moat.** Composio + your custom MCP + your preference profile + your history = an agent that can take higher-leverage actions in your name over time. You're not building a chatbot. You're building a digital proxy.

---

## 8. Decision Principles

These repeat across every instance. Make them defaults.

**Keep raw data forever.** Never delete. Never aggregate-and-drop. Storage is cheap; retroactive intelligence is impossible.

**Ingest broadly, act selectively.** Your ingestion should cover more than your current targeting. You can't see market trends in narrowly-filtered data. Filter at the scoring layer, not the ingestion layer.

**Every decision leaves a trace.** Event log table, always. Non-negotiable.

**Version prompts via git.** No DB-mirrored prompt table. `git rev-parse HEAD` in every event row.

**Tools are thin, CLAUDE.md is thick.** Business logic lives in policy (prompts), not in tool code. Tools should be replaceable in minutes without changing behavior.

**Adapter everything that touches the world.** Data source, output channels, auth providers — all behind interfaces. This lets you swap seed data for production, test channels for real ones, one provider for another — without touching the core.

**Three protocols only: DB wire, MCP, HTTP.** If you're tempted to build a fourth, you're creating glue.

**One invocation path for Claude.** The CLI. systemd, humans, future UIs — all funnel through it. Discipline centralized.

**Health and observability use the same channels as outputs.** Don't build a separate monitoring stack. Discord webhook for alerts → also Discord webhook for "system unhealthy."

**Defer the UI.** CLI is always enough for v1. UI comes when friction proves it.

---

## 9. Instance Recipe

To build a new personal leverage app:

**Step 1 — Name the problem.**
- What data source do you want watched?
- What decisions should Claude make about that data?
- What outputs do you need?
- How should you be notified?

**Step 2 — Design the 5 ingredients.**
- **Store:** tables for raw, features, outputs, outcomes, events, tokens. Start with the Upwork system's schema as a template; rename tables to domain terms.
- **Ingestion:** pick the data source adapter (API, RSS, scrape, inbox, DB). Write the `fetch()` implementation.
- **MCP tools:** list execution tools (list, get, save_decision, save_output) and world-action tools (send_alert, create_doc, etc.). Start with the Upwork system's tool list; add domain-specific tools.
- **CLAUDE.md:** write the rubric, output spec, voice, policies.
- **CLI:** define Claude-invoking vs. DB-direct commands. Use the same command framework as other instances.

**Step 3 — Copy the skeleton, not the contents.**
- `cp -r` an existing instance's monorepo structure.
- Replace domain-specific files: CLAUDE.md, MCP tool implementations, ingestion adapter, CLI command definitions.
- Keep the generic infrastructure: Docker Compose shape, systemd units, Drizzle scaffolding, CLI framework, auth flow.

**Step 4 — Build phases.**
- **Phase 0:** skeleton + seeded data on laptop. Validate the decision loop.
- **Phase 1:** live ingestion (real data source).
- **Phase 2:** VPS deployment.
- **Phase 3:** learning + intelligence tools.
- **Phase 4:** dashboard (only if CLI friction demands).

Each phase ships something usable. No "need all four before anything works."

---

## 10. Non-Patterns (When Not to Use This)

This architecture is wrong for:

- **Low-latency inference** (real-time classification, <100ms response). Claude Code invocations have ~1–5s overhead. If you need real-time, use a direct API call, not Claude Code.
- **Multi-user systems** (customers log in, you serve many). This pattern is single-operator by design. Multi-tenancy breaks the "CLAUDE.md is my personal policy" premise.
- **High-volume data pipelines** (>100k events/minute). The event log table wants bounded write rates. For true data engineering scale, use specialized tools.
- **Purely deterministic workflows** with no reasoning component. If your pipeline is "ingest → transform → store → serve," skip Claude entirely.

The sweet spot is: **personal-volume data + reasoning-heavy decisions + compounding value from historical context + outputs you consume rather than serve to others.**

---

## 11. Example Instances

Same architecture, different domains. Each is ~80% reusable from a prior instance.

| Instance | Ingestion source | Key tools | Output surface |
|---|---|---|---|
| **Upwork proposals** | Upwork GraphQL | score_job, draft_proposal, create_google_doc | Discord + CLI |
| **Inbound lead qualifier** | Gmail via Composio | score_lead, draft_reply, create_calendar_event | Discord + Gmail draft |
| **Content idea miner** | X/Reddit/HN scrapers | score_topic, draft_hook, get_trending_themes | Discord + dashboard |
| **Investment watch** | Prices + filings + news APIs | score_signal, explain_thesis | Discord (Tier S only) |
| **Meeting prep** | Calendar + CRM + LinkedIn | research_attendee, draft_brief | Morning Discord digest |
| **Cold outreach drafter** | Target list (CSV or DB) | research_prospect, draft_dm | Dashboard + clipboard |
| **Candidate tracker** | GitHub + LinkedIn + Stack Overflow | score_candidate, draft_outreach | Discord + dashboard |

All share: Postgres + pgvector substrate, same Docker Compose shape, same CLI framework, same event log discipline, same auth flow, same systemd timer pattern.

What differs: ingestion adapter, MCP tool set, CLAUDE.md contents.

---

## 12. What You Get By Following This

**Your code shrinks.** First instance takes weeks. Second takes days. Third takes hours.

**Your system compounds.** Six months of ingested data is a private intelligence asset. Two years is a moat.

**Your agent improves itself.** The retrospect loop means CLAUDE.md is a living document trained on your outcomes.

**Your attention is protected.** Alerts fire only when Claude decides they should. You spend time on decisions the system escalates, not on triage.

**Your proxy gets stronger.** Each new tool, each new CLAUDE.md revision, each new outcome makes Claude more capable of acting on your behalf.

---

## Appendix — Required Discipline

These are the disciplines without which the pattern degrades:

1. **Ingest everything, always.** Never let the pipeline skip rows "because they won't match our filter."
2. **Log every decision.** No "it was just a scoring call, don't log it." Log it.
3. **Version prompts via git.** Never edit CLAUDE.md without committing. `git rev-parse HEAD` in every event row.
4. **Use adapters for external dependencies.** Make swapping providers a config flip, not a refactor.
5. **One Claude-invocation function.** Every cron, every human command, every UI button — through the CLI.
6. **Keep the MCP server stateless.** No caches, no session, no hidden state. Every tool call is fresh.
7. **Health uses the output channels.** Same Discord, same CLI output. No separate monitoring.
8. **Defer the UI.** CLI is always enough for v1. Prove friction before building screens.
