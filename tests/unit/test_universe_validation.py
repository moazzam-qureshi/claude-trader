from pathlib import Path

import pytest
import yaml

from trading_sandwich.contracts.heartbeat import (
    UniverseEventType,
    UniverseMutationRequest,
)
from trading_sandwich.triage.universe_policy import (
    HardLimitViolation,
    apply_mutation,
    load_universe,
    validate_mutation,
)


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


def _write_policy(tmp_path: Path, payload=None) -> Path:
    payload = payload or SAMPLE_POLICY
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(payload))
    return p


def test_validate_blocks_unexclude_of_locked_symbol(tmp_path: Path):
    policy = load_universe(_write_policy(tmp_path))
    req = UniverseMutationRequest(
        event_type=UniverseEventType.UNEXCLUDE,
        symbol="SHIBUSDT",
        to_tier="observation",
        rationale="reconsidered after observation",
        reversion_criterion="re-exclude if no edge",
    )
    with pytest.raises(HardLimitViolation) as exc:
        validate_mutation(policy, req)
    assert "excluded_symbols_locked" in str(exc.value)


def test_validate_blocks_promote_into_core(tmp_path: Path):
    policy = load_universe(_write_policy(tmp_path))
    req = UniverseMutationRequest(
        event_type=UniverseEventType.PROMOTE,
        symbol="SOLUSDT",
        to_tier="core",
        rationale="proven over months of trades",
        reversion_criterion="demote on degradation",
    )
    with pytest.raises(HardLimitViolation) as exc:
        validate_mutation(policy, req)
    assert "core_promotions_operator_only" in str(exc.value)


def test_validate_blocks_when_total_universe_full(tmp_path: Path):
    payload = {
        "universe": {
            "tiers": {
                "core": {"symbols": [f"C{i}USDT" for i in range(4)]},
                "watchlist": {"symbols": [f"W{i}USDT" for i in range(8)]},
                "observation": {"symbols": [f"O{i}USDT" for i in range(8)]},
                "excluded": {"symbols": []},
            },
            "hard_limits": SAMPLE_POLICY["universe"]["hard_limits"],
        }
    }
    policy = load_universe(_write_policy(tmp_path, payload))
    req = UniverseMutationRequest(
        event_type=UniverseEventType.ADD,
        symbol="NEWUSDT",
        to_tier="observation",
        rationale="caught my eye in scans",
        reversion_criterion="remove if no signals in 21d",
    )
    with pytest.raises(HardLimitViolation) as exc:
        validate_mutation(policy, req)
    assert "max_total_universe_size" in str(exc.value)


def test_validate_blocks_when_per_tier_full(tmp_path: Path):
    payload = {
        "universe": {
            "tiers": {
                "core": {"symbols": []},
                "watchlist": {"symbols": []},
                "observation": {"symbols": [f"O{i}USDT" for i in range(12)]},
                "excluded": {"symbols": []},
            },
            "hard_limits": SAMPLE_POLICY["universe"]["hard_limits"],
        }
    }
    policy = load_universe(_write_policy(tmp_path, payload))
    req = UniverseMutationRequest(
        event_type=UniverseEventType.ADD,
        symbol="NEWUSDT",
        to_tier="observation",
        rationale="fits criteria, watching for setup",
        reversion_criterion="x",
    )
    with pytest.raises(HardLimitViolation) as exc:
        validate_mutation(policy, req)
    assert "max_per_tier" in str(exc.value)


def test_apply_mutation_add_writes_yaml_atomically(tmp_path: Path):
    policy_path = _write_policy(tmp_path)
    policy = load_universe(policy_path)
    req = UniverseMutationRequest(
        event_type=UniverseEventType.ADD,
        symbol="ARBUSDT",
        to_tier="observation",
        rationale="fits criteria, watching for setup",
        reversion_criterion="remove if no signals in 21d",
    )
    apply_mutation(policy_path, policy, req)
    reread = yaml.safe_load(policy_path.read_text())
    assert "ARBUSDT" in reread["universe"]["tiers"]["observation"]["symbols"]


def test_apply_mutation_demote_moves_symbol(tmp_path: Path):
    policy_path = _write_policy(tmp_path)
    policy = load_universe(policy_path)
    req = UniverseMutationRequest(
        event_type=UniverseEventType.DEMOTE,
        symbol="SOLUSDT",
        to_tier="observation",
        rationale="momentum failing repeatedly",
        reversion_criterion="repromote if breaks back out",
    )
    apply_mutation(policy_path, policy, req)
    reread = yaml.safe_load(policy_path.read_text())
    assert "SOLUSDT" not in reread["universe"]["tiers"]["watchlist"]["symbols"]
    assert "SOLUSDT" in reread["universe"]["tiers"]["observation"]["symbols"]
