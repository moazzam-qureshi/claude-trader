"""`cli settings ...` subcommands for the three-tier policy repo.

Operator-side terminal interface mirroring the Discord /settings surface:

    cli settings bootstrap                     # idempotent first-boot seed
    cli settings reseed --key K                # restore K to YAML default
    cli settings list [--prefix P]             # current values
    cli settings get K                         # one value + tier marker

`bootstrap` and `reseed` go through `settings.seed`. `list` and `get`
read directly via `settings.repo.get` / direct DB query — same paths
the MCP and Discord surfaces use.

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md \xc2\xa712.
"""
from __future__ import annotations

import asyncio

import typer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.settings import _halal, _safety_seed, seed
from trading_sandwich.settings.keys import tier_of


settings_app = typer.Typer(help="Three-tier policy settings (Tier 2 + Tier 3)")


@settings_app.command("bootstrap")
def settings_bootstrap() -> None:
    """Idempotent first-boot seed of policy_settings from policy.yaml.

    Existing rows are NEVER overwritten. Tier 1 halal keys are excluded
    (they remain file-only). Use `cli settings reseed --key K` to
    restore one key to its YAML default.
    """
    try:
        report = asyncio.run(seed.bootstrap())
    except Exception as exc:
        typer.echo(f"bootstrap: FAIL — {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"bootstrap: {report.inserted_count} inserted, "
        f"{report.skipped_count} skipped (already present), "
        f"{report.reseeded_count} reseeded"
    )


@settings_app.command("reseed")
def settings_reseed(
    key: str = typer.Option(..., "--key", help="Dotted-path key to restore"),
) -> None:
    """Restore one key to its policy.yaml default value (operator-only path).

    Writes a `policy_changes` audit row with `changed_by='seed'`. Use
    when Claude's tuning has gone wrong and you want the YAML default
    back without digging through the audit log.
    """
    try:
        report = asyncio.run(seed.bootstrap(force_reseed_keys=[key]))
    except seed.NoYamlDefaultError as exc:
        typer.echo(f"reseed: FAIL — no_default for key {key!r}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except _halal.HalalViolationError as exc:
        typer.echo(f"reseed: FAIL — halal_inviolable: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.echo(f"reseed: FAIL — {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"reseed: {report.reseeded_count} reseeded ({key})")


@settings_app.command("list")
def settings_list(
    prefix: str = typer.Option("", "--prefix", help="Filter to keys starting with this prefix"),
) -> None:
    """Print all policy_settings rows (Tier 2 + Tier 3) sorted by key."""
    sql = "SELECT key, value, value_type, updated_by FROM policy_settings"
    params: dict = {}
    if prefix:
        sql += " WHERE key LIKE :p"
        params["p"] = f"{prefix}%"
    sql += " ORDER BY key"

    async def _go():
        url = get_settings().database_url
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(text(sql), params)
                return list(r)
        finally:
            await engine.dispose()

    rows = asyncio.run(_go())
    if not rows:
        typer.echo(f"(no settings under prefix {prefix!r})")
        return
    for k, v, t, ub in rows:
        tier = tier_of(k)
        marker = "T2" if tier == 2 else "T3"
        typer.echo(f"{k}\t{v!r}\t({t}, {marker}, by={ub})")


@settings_app.command("get")
def settings_get(key: str) -> None:
    """Print one setting with tier marker. Tier 1 reads from policy.yaml."""
    tier = tier_of(key)

    if tier == 1:
        try:
            v = _halal.read(key)
        except KeyError:
            typer.echo(f"key_not_found: {key}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"{key} = {v!r} (Tier 1 halal, file-only — inviolable)")
        return

    async def _read_db():
        url = get_settings().database_url
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(
                    text(
                        "SELECT value, value_type, updated_at, updated_by "
                        "FROM policy_settings WHERE key = :k"
                    ),
                    {"k": key},
                )
                return r.first()
        finally:
            await engine.dispose()

    row = asyncio.run(_read_db())
    if row is not None:
        v, vtype, updated_at, updated_by = row
        marker = "Tier 2 safety" if tier == 2 else "Tier 3"
        typer.echo(
            f"{key} = {v!r} ({vtype}, {marker}, by={updated_by}, "
            f"updated_at={updated_at.isoformat() if updated_at else 'n/a'})"
        )
        return

    if tier == 2:
        try:
            v = _safety_seed.read(key)
        except KeyError:
            typer.echo(f"key_not_found: {key}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"{key} = {v!r} (Tier 2 safety, file seed — no DB override)")
        return

    typer.echo(f"key_not_found: {key}", err=True)
    raise typer.Exit(code=1)
