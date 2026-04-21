# Trading Sandwich — Agent Policy (Phase 0 stub)

Phase 0 does not invoke Claude. This file exists to establish the discipline
that every prompt change is a commit. Content will be expanded in Phase 2.

## Placeholder sections (to be filled in Phase 2)

- Role and operating modes (triage | analyze | retrospect | ad_hoc)
- Decision rubric
- Output spec
- Voice and tone
- Decision policies
- Tool reference

## Hard rules (apply from day one)

- Always call `find_similar_signals` before finalizing a decision.
- Never modify a stop-loss looser than the original.
- Never submit an order without an attached stop.
