"""Phase 3 universe shape tests — spec §6.1.

The Phase 3 pivot renames the second tier from `watchlist` to `active`,
expands it to the full halal candidate set (~22 symbols across L1s, L2s,
DePIN, AI, and currency), and sub-categorizes `excluded` into
`symbols_lending` / `symbols_perp_protocols` / `symbols_memecoins` so
the reason for exclusion is encoded in the data structure rather than
buried in a free-text `reason` field.

These tests pin the spec §6.1 contract on the loader and the
mutation logic. They are the RED for plan Task 1.5.
"""
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


SPEC_6_1_POLICY = {
    "universe": {
        "tiers": {
            "core": {
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                "size_multiplier": 1.0,
                "max_concurrent_positions": 4,
                "shift_attention": "every_shift",
            },
            "active": {
                "symbols": [
                    # L1s
                    "AVAXUSDT", "ADAUSDT", "NEARUSDT", "APTUSDT", "SUIUSDT",
                    "ATOMUSDT", "ALGOUSDT", "DOTUSDT",
                    # L2s
                    "ARBUSDT", "OPUSDT", "POLUSDT", "IMXUSDT", "STRKUSDT",
                    # DePIN / infra
                    "LINKUSDT", "FILUSDT", "RNDRUSDT", "GRTUSDT",
                    # AI
                    "TAOUSDT", "FETUSDT", "WLDUSDT",
                    # Currency
                    "LTCUSDT", "BCHUSDT",
                ],
                "size_multiplier": 0.5,
                "max_concurrent_positions": 12,
                "shift_attention": "time_permitting",
            },
            "observation": {
                "symbols": [
                    "HNTUSDT", "AKTUSDT", "AGIXUSDT", "OCEANUSDT",
                    "DASHUSDT", "ZECUSDT", "INJUSDT",
                ],
                "size_multiplier": 0.0,
                "max_concurrent_positions": 0,
                "shift_attention": "weekly_sweep",
            },
            "excluded": {
                "symbols_lending": [
                    "AAVEUSDT", "COMPUSDT", "MKRUSDT",
                    "LDOUSDT", "CRVUSDT", "CAKEUSDT",
                ],
                "symbols_perp_protocols": [
                    "GMXUSDT", "DYDXUSDT", "GNSUSDT",
                ],
                "symbols_memecoins": [
                    "SHIBUSDT", "PEPEUSDT", "BONKUSDT",
                    "WIFUSDT", "FLOKIUSDT", "DOGEUSDT",
                ],
                "reason": (
                    "haram (riba on lending; perp structure on derivatives) "
                    "or operator policy (memecoins)"
                ),
            },
        },
        "hard_limits": {
            "min_24h_volume_usd_floor": 40_000_000,
            "vol_30d_annualized_max_ceiling": 3.00,
            "excluded_symbols_locked": [
                "SHIBUSDT", "PEPEUSDT", "BONKUSDT", "WIFUSDT",
                "FLOKIUSDT", "DOGEUSDT", "AAVEUSDT", "COMPUSDT",
                "MKRUSDT", "LDOUSDT", "CRVUSDT", "CAKEUSDT",
                "GMXUSDT", "DYDXUSDT", "GNSUSDT",
            ],
            "core_promotions_operator_only": True,
            "max_total_universe_size": 40,
            "max_per_tier": {"core": 5, "active": 25, "observation": 15},
        },
    }
}


def _write_policy(tmp_path: Path, payload=None) -> Path:
    payload = payload or SPEC_6_1_POLICY
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(payload))
    return p


def test_loader_recognizes_active_tier(tmp_path: Path):
    """`active` is the new name for the trading-eligible second tier."""
    policy = load_universe(_write_policy(tmp_path))
    assert "AVAXUSDT" in policy.tiers["active"]
    assert "LINKUSDT" in policy.tiers["active"]
    assert len(policy.tiers["active"]) == 22


def test_loader_does_not_expose_legacy_watchlist_tier(tmp_path: Path):
    """The `watchlist` name is gone — code reads `active` only."""
    policy = load_universe(_write_policy(tmp_path))
    assert "watchlist" not in policy.tiers


def test_loader_flattens_subcategorized_excluded(tmp_path: Path):
    """`excluded.symbols_*` sublists flatten into one excluded set for
    membership checks. Sub-categorization is preserved on disk so the
    REASON for exclusion is queryable, but the loader exposes a flat
    list at policy.tiers['excluded']."""
    policy = load_universe(_write_policy(tmp_path))
    excluded = policy.tiers["excluded"]
    assert "AAVEUSDT" in excluded            # lending bucket
    assert "GMXUSDT" in excluded             # perp_protocols bucket
    assert "SHIBUSDT" in excluded            # memecoins bucket
    assert len(excluded) == 6 + 3 + 6        # 15 total across 3 sublists


def test_total_size_excludes_excluded_tier(tmp_path: Path):
    """total_size() spans core + active + observation only (excluded
    symbols are not 'in' the universe — they are explicitly out)."""
    policy = load_universe(_write_policy(tmp_path))
    # core (3) + active (22) + observation (7) = 32
    assert policy.total_size == 32


def test_tier_of_returns_active_for_active_symbols(tmp_path: Path):
    policy = load_universe(_write_policy(tmp_path))
    assert policy.tier_of("AVAXUSDT") == "active"
    assert policy.tier_of("BTCUSDT") == "core"
    assert policy.tier_of("HNTUSDT") == "observation"
    assert policy.tier_of("AAVEUSDT") == "excluded"
    assert policy.tier_of("UNKNOWNUSDT") is None


def test_validate_blocks_promote_to_active_when_full(tmp_path: Path):
    """max_per_tier.active = 25; current 22; promoting 4 from observation
    would overflow the 25-cap once 26th lands."""
    payload = {
        "universe": {
            "tiers": {
                "core": {"symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]},
                "active": {"symbols": [f"A{i}USDT" for i in range(25)]},
                "observation": {"symbols": ["NEARUSDT"]},
                "excluded": {
                    "symbols_lending": [],
                    "symbols_perp_protocols": [],
                    "symbols_memecoins": [],
                },
            },
            "hard_limits": SPEC_6_1_POLICY["universe"]["hard_limits"],
        }
    }
    policy = load_universe(_write_policy(tmp_path, payload))
    req = UniverseMutationRequest(
        event_type=UniverseEventType.PROMOTE,
        symbol="NEARUSDT",
        to_tier="active",
        rationale="proven over 30 days in observation",
        reversion_criterion="demote on degradation",
    )
    with pytest.raises(HardLimitViolation) as exc:
        validate_mutation(policy, req)
    assert "max_per_tier" in str(exc.value)


def test_apply_mutation_promote_observation_to_active(tmp_path: Path):
    policy_path = _write_policy(tmp_path)
    policy = load_universe(policy_path)
    req = UniverseMutationRequest(
        event_type=UniverseEventType.PROMOTE,
        symbol="HNTUSDT",
        to_tier="active",
        rationale="enough signals fired in observation window",
        reversion_criterion="demote if 30d signals dry up",
    )
    apply_mutation(policy_path, policy, req)
    reread = yaml.safe_load(policy_path.read_text())
    assert "HNTUSDT" not in reread["universe"]["tiers"]["observation"]["symbols"]
    assert "HNTUSDT" in reread["universe"]["tiers"]["active"]["symbols"]


def test_apply_mutation_exclude_routes_to_memecoins_default(tmp_path: Path):
    """When EXCLUDE is called without an explicit subcategory, the
    symbol lands in `symbols_memecoins` — the most common operator-policy
    exclusion bucket. (Lending and perp-protocol exclusions are
    structural-haram and would be caught at universe-add time, not via
    runtime EXCLUDE.)"""
    policy_path = _write_policy(tmp_path)
    policy = load_universe(policy_path)
    req = UniverseMutationRequest(
        event_type=UniverseEventType.EXCLUDE,
        symbol="HNTUSDT",
        rationale="operator-policy excluded after observation review",
        reversion_criterion="never",
    )
    apply_mutation(policy_path, policy, req)
    reread = yaml.safe_load(policy_path.read_text())
    excluded = reread["universe"]["tiers"]["excluded"]
    assert "HNTUSDT" not in reread["universe"]["tiers"]["observation"]["symbols"]
    assert "HNTUSDT" in excluded["symbols_memecoins"]
    # The other two buckets stay intact and unchanged.
    assert "AAVEUSDT" in excluded["symbols_lending"]
    assert "GMXUSDT" in excluded["symbols_perp_protocols"]


def test_apply_mutation_remove_from_active_persists(tmp_path: Path):
    policy_path = _write_policy(tmp_path)
    policy = load_universe(policy_path)
    req = UniverseMutationRequest(
        event_type=UniverseEventType.REMOVE,
        symbol="AVAXUSDT",
        rationale="demoted out of universe entirely",
        reversion_criterion="re-add if structure improves",
    )
    apply_mutation(policy_path, policy, req)
    reread = yaml.safe_load(policy_path.read_text())
    assert "AVAXUSDT" not in reread["universe"]["tiers"]["active"]["symbols"]


def test_real_policy_yaml_matches_spec_6_1(tmp_path: Path):
    """The committed policy.yaml in the repo conforms to spec §6.1
    structure: core/active/observation/excluded with subcategorized
    excluded buckets, and the full halal candidate roster."""
    policy_path = Path("/app/policy.yaml")
    policy = load_universe(policy_path)
    # Tier structure
    assert set(policy.tiers.keys()) == {"core", "active", "observation", "excluded"}
    # Roster sanity (a sampling — not pinning exact roster to keep this
    # test resilient to operator-driven roster changes)
    assert "BTCUSDT" in policy.tiers["core"]
    assert "ETHUSDT" in policy.tiers["core"]
    assert "SOLUSDT" in policy.tiers["core"]
    assert "AVAXUSDT" in policy.tiers["active"]
    assert "LINKUSDT" in policy.tiers["active"]
    assert "AAVEUSDT" in policy.tiers["excluded"]   # lending
    assert "GMXUSDT" in policy.tiers["excluded"]    # perp protocols
    assert "SHIBUSDT" in policy.tiers["excluded"]   # memecoins
    # Sub-categorization preserved on disk
    raw = yaml.safe_load(policy_path.read_text())
    excluded = raw["universe"]["tiers"]["excluded"]
    assert "symbols_lending" in excluded
    assert "symbols_perp_protocols" in excluded
    assert "symbols_memecoins" in excluded
