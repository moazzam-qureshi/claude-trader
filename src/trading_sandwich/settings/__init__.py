"""DB-backed policy settings package.

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md for the
three-tier mutability model:

- Tier 1 (halal): file-only, NEVER mutable. Read via `_halal`.
- Tier 2 (safety): file seed + operator-only DB override. Read via repo
  (DB row wins; falls back to `_safety_seed` file). Write only with
  `authority='operator_safety'`.
- Tier 3 (default): DB-backed. Read via repo. Write via `set_setting`.

Public API: `keys` for tier classification, `repo` for typed get/set,
`snapshot` for decision-row provenance, `seed` for first-boot bootstrap.
"""
