# trading-sandwich

24/7 crypto market analysis + execution system, built as an instance of the
MCP-Sandwich pattern (see `architecture.md`).

See `docs/superpowers/specs/` for the current design and
`docs/superpowers/plans/` for phased implementation plans.

## Quickstart (Phase 0)

    cp .env.example .env         # fill in values
    docker compose up -d
    docker compose run --rm cli doctor
