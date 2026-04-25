import asyncio
from pathlib import Path

import pytest
import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import UniverseEvent


SAMPLE_POLICY = {
    "universe": {
        "tiers": {
            "core": {"symbols": ["BTCUSDT", "ETHUSDT"]},
            "watchlist": {"symbols": ["SOLUSDT"]},
            "observation": {"symbols": []},
            "excluded": {"symbols": ["SHIBUSDT"]},
        },
        "hard_limits": {
            "min_24h_volume_usd_floor": 100_000_000,
            "vol_30d_annualized_max_ceiling": 3.0,
            "excluded_symbols_locked": ["SHIBUSDT"],
            "core_promotions_operator_only": True,
            "max_total_universe_size": 20,
            "max_per_tier": {"core": 4, "watchlist": 8, "observation": 12},
        },
    }
}


def _stub_discord(monkeypatch):
    posted: list = []

    async def _fake_post(card):
        posted.append(card)
        return "fake_message_id"

    monkeypatch.setattr(
        "trading_sandwich.mcp.tools.universe._post_card", _fake_post
    )
    return posted


def _stub_metrics_pass(monkeypatch):
    async def _ok(symbol):
        return {"volume_24h_usd": 250_000_000, "vol_30d_annualized": 1.0}
    monkeypatch.setattr("trading_sandwich.mcp.tools.universe._fetch_metrics", _ok)


@pytest.mark.integration
def test_mutate_add_persists_event_and_yaml_and_discord(env_for_postgres, tmp_path: Path, monkeypatch):
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(SAMPLE_POLICY))
    monkeypatch.setattr("trading_sandwich.mcp.tools.universe.POLICY_PATH", p)
    posted = _stub_discord(monkeypatch)
    _stub_metrics_pass(monkeypatch)

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        from trading_sandwich.mcp.tools.universe import mutate_universe

        async def _run():
            return await mutate_universe(
                event_type="add",
                symbol="ARBUSDT",
                to_tier="observation",
                rationale="caught in 24h scans, fits criteria",
                reversion_criterion="remove if no signals in 21d",
                shift_id=None,
            )

        result = asyncio.run(_run())
        assert result["accepted"] is True
        assert result["event_id"] is not None

        reread = yaml.safe_load(p.read_text())
        assert "ARBUSDT" in reread["universe"]["tiers"]["observation"]["symbols"]

        async def _query():
            factory = get_session_factory()
            async with factory() as session:
                return (await session.execute(select(UniverseEvent))).scalars().all()

        events = asyncio.run(_query())
        assert any(e.event_type == "add" and e.symbol == "ARBUSDT" for e in events)
        assert len(posted) == 1


@pytest.mark.integration
def test_mutate_blocked_records_hard_limit_event(env_for_postgres, tmp_path: Path, monkeypatch):
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(SAMPLE_POLICY))
    monkeypatch.setattr("trading_sandwich.mcp.tools.universe.POLICY_PATH", p)
    posted = _stub_discord(monkeypatch)

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        from trading_sandwich.mcp.tools.universe import mutate_universe

        async def _run():
            return await mutate_universe(
                event_type="unexclude",
                symbol="SHIBUSDT",
                to_tier="observation",
                rationale="reconsidering after observation",
                reversion_criterion="re-exclude if no edge",
                shift_id=None,
            )

        result = asyncio.run(_run())
        assert result["accepted"] is False
        assert "excluded_symbols_locked" in result["blocked_by"]

        async def _query():
            factory = get_session_factory()
            async with factory() as session:
                return (await session.execute(select(UniverseEvent))).scalars().all()

        events = asyncio.run(_query())
        blocked = [e for e in events if e.event_type == "hard_limit_blocked"]
        assert len(blocked) == 1
        assert blocked[0].blocked_by == "excluded_symbols_locked"
        assert len(posted) == 1
