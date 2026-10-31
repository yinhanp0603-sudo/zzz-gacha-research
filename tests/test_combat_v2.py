"""机制匹配与定向支援的战斗情景契约。"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

from zzz_research.combat import proxy as P
from zzz_research.combat import benchmark as BM
from zzz_research.contracts import CombatRequest, EvalStatus


@pytest.fixture()
def combat_cfg():
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "config/modules/combat.json").read_text(encoding="utf-8"))
    cfg["model_version"] = "proxy_v2_observed_anchor"
    cfg["research_v2"] = {
        "mechanism_strength": 1.0,
        "mechanisms": {
            "defense": {"penalty": 1.0},
            "window": {"penalty": 0.4},
            "barrier": {"penalty": 0.6},
        },
        "unit_profiles": {
            "A": {"affinities": {"defense": 0.0, "window": 0.0, "barrier": 0.0}},
            "B": {"affinities": {"defense": 0.9, "window": 0.9, "barrier": 0.9}},
        },
        "support_pairs": {
            "A": {"S_A": {"net_gain": 0.25, "match_lift": {"window": 0.3, "barrier": 0.2}}},
            "B": {"S_A": {"net_gain": 0.05, "match_lift": {}}},
        },
    }
    return cfg


def _scenario(params, kind="window", *, strength=None, theta=0.4, scale=1.0):
    return P.reference_scenario(
        params, "battle", theta=theta, uptime=1.0,
        support_gain_scale=scale, mechanism_type=kind, mechanism_strength=strength)


def _damage(params, scenario, *, anchor="A", support=None, pierce=0.0, cinema=0):
    return P.window_damage(
        params, scenario, b_ref=params.hp_index, pierce_share=pierce, gear=1.0,
        cinema=cinema, signature_weapon=False, support_unit=support, anchor_unit=anchor)


def test_legacy_branch_ignores_mechanism_profiles(combat_cfg):
    combat_cfg["model_version"] = "proxy_v1"
    with_profiles = P.ProxyParams.from_config(combat_cfg)
    without_profiles = deepcopy(combat_cfg)
    without_profiles.pop("research_v2")
    legacy = P.ProxyParams.from_config(without_profiles)
    s = _scenario(with_profiles, "window", strength=2.0)
    assert _damage(with_profiles, s, support="S_A") == _damage(legacy, s, support="S_A")


@pytest.mark.parametrize("pierce", [0.0, 1.0])
def test_window_lock_applies_independently_of_defense_bypass(combat_cfg, pierce):
    params = P.ProxyParams.from_config(combat_cfg)
    baseline = _damage(params, _scenario(params, strength=0.0), pierce=pierce)
    locked = _damage(params, _scenario(params), pierce=pierce)
    assert locked.clear_index / baseline.clear_index == pytest.approx(math.exp(-0.4))


def test_defense_lock_retains_reference_formula(combat_cfg):
    params = P.ProxyParams.from_config(combat_cfg)
    locked = _damage(params, _scenario(params, "defense", theta=1.0))
    assert locked.clear_index == pytest.approx(P.normal_shield_retention(params))
    piercing = _damage(params, _scenario(params, "defense", theta=1.0), pierce=1.0)
    assert piercing.clear_index == pytest.approx(1.0 - params.shield_damage_reduction)


@pytest.mark.parametrize("theta", [0.0, 0.4, 1.0])
def test_barrier_penalty_only_applies_during_covered_output(combat_cfg, theta):
    params = P.ProxyParams.from_config(combat_cfg)
    scenario = _scenario(params, "barrier", theta=theta)
    result = _damage(params, scenario, pierce=1.0)
    q = P.shield_damage_share(theta, scenario.kappa)
    assert result.clear_index == pytest.approx((1.0 - q) + q * math.exp(-0.6))


@pytest.mark.parametrize("anchor,support,expected", [
    ("A", "S_A", 1.25), ("B", "S_A", 1.05), ("A", "S_B", 1.0),
])
def test_support_net_uplift_is_pair_specific(combat_cfg, anchor, support, expected):
    params = P.ProxyParams.from_config(combat_cfg)
    result = _damage(params, _scenario(params, strength=0.0), anchor=anchor, support=support)
    assert result.clear_index == pytest.approx(expected)


def test_support_numeric_scale_does_not_change_counter_capability(combat_cfg):
    params = P.ProxyParams.from_config(combat_cfg)
    low = _damage(params, _scenario(params, scale=0.8), support="S_A")
    high = _damage(params, _scenario(params, scale=1.6), support="S_A")
    assert low.breakdown["mechanism_match"] == high.breakdown["mechanism_match"] == 0.3
    assert low.breakdown["mechanism_retained_output"] == high.breakdown["mechanism_retained_output"]
    assert high.clear_index / low.clear_index == pytest.approx(1.4 / 1.2)
    no_support = _damage(params, _scenario(params))
    assert low.breakdown["mechanism_retained_output"] > no_support.breakdown["mechanism_retained_output"]


def test_character_investment_does_not_compress_barrier_coverage(combat_cfg):
    params = P.ProxyParams.from_config(combat_cfg)
    scenario = _scenario(params, "barrier")
    base = _damage(params, scenario)
    high = _damage(params, scenario, cinema=2)
    assert base.breakdown["q"] == high.breakdown["q"]
    assert base.breakdown["mechanism_retained_output"] == high.breakdown["mechanism_retained_output"]
    assert high.clear_index / base.clear_index == pytest.approx(params.cinema_multipliers[2])


def test_scenario_strength_overrides_global_strength(combat_cfg):
    params = P.ProxyParams.from_config(combat_cfg)
    result = _damage(params, _scenario(params, strength=2.0))
    assert result.clear_index == pytest.approx(math.exp(-0.8))
    assert result.breakdown["mechanism_strength"] == 2.0


def test_engine_passes_anchor_into_pair_and_mechanism_resolution(combat_cfg):
    params = P.ProxyParams.from_config(combat_cfg)
    scenario = _scenario(params)
    model = P.ProxyCombatModel(combat_cfg, [scenario])
    result = model.evaluate(CombatRequest(
        team=("A", "fixed", "S_A"), anchor_unit_id="A",
        investment={"B_ref": params.hp_index, "pierce_share": 1.0,
                    "cinema": 0, "signature_weapon": False},
        actual_g=1.0, scenario_id=scenario.scenario_id, model_version=params.model_version))
    assert result.eval_status is EvalStatus.OK
    assert result.ci == pytest.approx(_damage(params, scenario, support="S_A", pierce=1.0).clear_index)
    assert result.damage_breakdown["mechanism_match"] == 0.3


def test_unregistered_anchor_is_not_given_a_generic_profile(combat_cfg):
    params = P.ProxyParams.from_config(combat_cfg)
    with pytest.raises(P.CombatInputError, match="缺少主核"):
        _damage(params, _scenario(params), anchor="unregistered")


@pytest.mark.parametrize("field", ["affinity", "pair"])
def test_invalid_research_profile_is_rejected(combat_cfg, field):
    if field == "affinity":
        combat_cfg["research_v2"]["unit_profiles"]["A"]["affinities"]["window"] = 1.1
    else:
        combat_cfg["research_v2"]["support_pairs"]["missing"] = {"S_A": {"net_gain": 0.2, "match_lift": {}}}
    with pytest.raises(P.CombatInputError):
        P.ProxyParams.from_config(combat_cfg)


def test_fixed_benchmark_preserves_window_channel_and_scenario_strength(combat_cfg):
    model = P.ProxyCombatModel(combat_cfg)
    scenario = _scenario(model.params, strength=1.7)
    rows = BM.build_fixed_benchmark_panel(
        model, [BM.BenchmarkAnchor("A", model.params.hp_index, 1.0)],
        [BM.BenchmarkEnvironment("window", scenario.hp, scenario.theta, scenario.uptime,
                                 scenario.mechanism_type, scenario.mechanism_strength)],
        neutral_fixed="fixed", neutral_flex="flex",
        investments=[BM.Investment(0, False)], supports=[None, "S_A"], kappas=[scenario.kappa])
    assert len(rows) == 2
    for row in rows:
        expected = _damage(model.params, scenario, support=row.support, pierce=1.0)
        assert row.ci == pytest.approx(expected.clear_index)


def test_benchmark_identity_includes_strength_without_an_explicit_channel(combat_cfg):
    model = P.ProxyCombatModel(combat_cfg)
    anchors = [BM.BenchmarkAnchor("A", model.params.hp_index, 0.0)]
    def identity(strength):
        environment = BM.BenchmarkEnvironment("default", model.params.hp_index, 0.4, 1.0,
                                             mechanism_strength=strength)
        return BM.benchmark_id(model, anchors, [environment], [1.0], "fixed", "flex")
    assert identity(0.5) != identity(1.5)
