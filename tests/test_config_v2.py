"""研究情景配置的引用、观察窗与版本消融约束。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from zzz_research.config import ConfigError, resolve_config
from zzz_research.analytics import search as SE
from zzz_research.combat import proxy as P
from zzz_research.contracts import SourceKind
from zzz_research.provenance import build_parameter_registry
from zzz_research.simulation import resolve_world


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def research_copy(tmp_path):
    path = tmp_path / "research_v2"
    shutil.copytree(ROOT / "config/research_v2", path)
    return path


def _edit(path, module, change):
    target = path / "modules" / f"{module}.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    change(data)
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _resolve(path):
    return resolve_config(path / "experiment.json")


def test_legacy_and_research_configs_resolve_separately(research_copy):
    legacy = resolve_config(ROOT / "config/experiment.json")
    research = _resolve(research_copy)
    assert legacy.config_id != research.config_id
    assert research.get("combat.model_version").startswith("proxy_v2")
    assert research.get("behavior.model_version").startswith("behavior_v2")
    assert research.get("scenarios.research_v2.observation_periods") <= research.get("scenarios.timeline.n_periods")


@pytest.mark.parametrize("module", ["combat", "behavior", "scenarios"])
def test_active_v2_requires_its_research_block(research_copy, module):
    _edit(research_copy, module, lambda data: data.pop("research_v2"))
    with pytest.raises(ConfigError, match="research_v2"):
        _resolve(research_copy)


def test_extension_does_not_make_legacy_required_keys_optional(research_copy):
    _edit(research_copy, "combat", lambda data: data.pop("shield"))
    with pytest.raises(ConfigError, match="缺失字段.*shield"):
        _resolve(research_copy)


def test_missing_main_profile_is_rejected_before_simulation(research_copy):
    _edit(research_copy, "combat", lambda data: data["research_v2"]["unit_profiles"].pop("starter_dps"))
    with pytest.raises(ConfigError, match="starter_dps"):
        _resolve(research_copy)


def test_unknown_main_profile_is_rejected(research_copy):
    def change(data):
        profiles = data["research_v2"]["unit_profiles"]
        profiles["unknown_dps"] = profiles["starter_dps"]
    _edit(research_copy, "combat", change)
    with pytest.raises(ConfigError, match="未知主核"):
        _resolve(research_copy)


def test_support_pair_must_reference_a_support_product(research_copy):
    def change(data):
        data["support"]["gains"]["DPS_01"] = 0.2
        data["research_v2"]["support_pairs"].setdefault("starter_dps", {})["DPS_01"] = {
            "net_gain": 0.2, "match_lift": {}}
    _edit(research_copy, "combat", change)
    with pytest.raises(ConfigError, match="role=support"):
        _resolve(research_copy)


def test_combat_numeric_validation_uses_proxy_rules(research_copy):
    def change(data):
        data["research_v2"]["unit_profiles"]["starter_dps"]["affinities"]["window"] = 1.1
    _edit(research_copy, "combat", change)
    with pytest.raises(ConfigError, match=r"\[0,1\]"):
        _resolve(research_copy)


def test_behavior_numeric_validation_uses_spending_rules(research_copy):
    def change(data):
        data["research_v2"]["spending"]["topup"]["fatigue_weight"] = -1
    _edit(research_copy, "behavior", change)
    with pytest.raises(ConfigError, match="非负"):
        _resolve(research_copy)


def test_behavior_spending_typo_is_not_silently_ignored(research_copy):
    def change(data):
        data["research_v2"]["spending"]["subscription"]["fatigue_weigth"] = 1
    _edit(research_copy, "behavior", change)
    with pytest.raises(ConfigError, match="subscription.*字段"):
        _resolve(research_copy)


@pytest.mark.parametrize("observation", [0, 1000])
def test_observation_window_stays_within_the_supply_timeline(research_copy, observation):
    _edit(research_copy, "scenarios", lambda data: data["research_v2"].update(observation_periods=observation))
    with pytest.raises(ConfigError, match="observation_periods"):
        _resolve(research_copy)


def test_mechanism_schedule_must_cover_every_supply_period(research_copy):
    _edit(research_copy, "scenarios", lambda data: data["research_v2"]["mechanism_schedule"].pop("2"))
    with pytest.raises(ConfigError, match="覆盖时间轴每一期"):
        _resolve(research_copy)


def test_schedule_mechanism_must_be_registered_in_active_combat(research_copy):
    def change(data):
        data["research_v2"]["mechanisms"].pop("barrier")
        for profile in data["research_v2"]["unit_profiles"].values():
            profile["affinities"].pop("barrier")
        for pairs in data["research_v2"]["support_pairs"].values():
            for pair in pairs.values():
                pair["match_lift"].pop("barrier", None)
    _edit(research_copy, "combat", change)
    with pytest.raises(ConfigError, match="schedule.*未登记机制"):
        _resolve(research_copy)


def test_optional_blocks_do_not_block_legacy_ablation_or_shorten_supply(research_copy):
    original = _resolve(research_copy)
    _edit(research_copy, "combat", lambda data: data.update(model_version="proxy_v1"))
    _edit(research_copy, "behavior", lambda data: data.update(model_version="behavior_v1"))
    _edit(research_copy, "scenarios", lambda data: data["research_v2"].update(observation_periods=10))
    ablation = _resolve(research_copy)
    assert ablation.get("scenarios.research_v2.observation_periods") == 10
    assert ablation.get("scenarios.timeline.n_periods") == original.get("scenarios.timeline.n_periods")
    assert ablation.get("scenarios.events") == original.get("scenarios.events")
    assert ablation.config_id != original.config_id


def test_pair_parameters_are_part_of_scientific_identity(research_copy):
    original = _resolve(research_copy)
    def change(data):
        pairs = next(iter(data["research_v2"]["support_pairs"].values()))
        pair = next(iter(pairs.values()))
        pair["net_gain"] += 0.01
    _edit(research_copy, "combat", change)
    assert _resolve(research_copy).config_id != original.config_id


def test_search_lambda_reaches_the_active_window_damage_channel(research_copy):
    base = _resolve(research_copy)
    perturbation = SE.Perturbation("P0", None, 1.0, "基线")
    ci = []
    for strength in (0.0, 2.0):
        candidate = SE.Candidate(f"L{strength:g}_A1", strength, 1.0)
        overrides = SE.run_overrides(base, candidate, perturbation, {})
        assert overrides["combat.research_v2.mechanism_strength"] == strength
        assert not set(SE.LAMBDA_PARAMS).intersection(overrides)
        cfg = resolve_config(research_copy / "experiment.json", overrides=overrides)
        model = P.ProxyCombatModel(cfg.module("combat"))
        world = resolve_world(cfg, model)
        scenario = next(p.scenario for p in world.periods if p.scenario.mechanism_type == "window")
        result = P.window_damage(
            model.params, scenario, b_ref=model.params.hp_index, pierce_share=1.0, gear=1.0,
            cinema=0, signature_weapon=False, support_unit=None, anchor_unit="starter_dps")
        assert result.breakdown["mechanism_strength"] == strength
        ci.append(result.clear_index)
    assert ci[0] > ci[1]


def test_parameter_registry_keeps_conditional_anchor_source_reference(research_copy):
    cfg = _resolve(research_copy)
    provenance = cfg.metadata["module_metadata"]["combat"]["provenance"]
    prefix = "combat.research_v2.support_pairs."
    records = [record for record in build_parameter_registry(cfg) if record.parameter_name.startswith(prefix)]
    assert records
    assert all(record.source_ref == provenance["research_v2.support_pairs"] for record in records)
    assert all(record.source_kind is SourceKind.SCENARIO_ASSUMED for record in records)


def test_parameter_registry_prefers_the_longest_matching_source_prefix(research_copy):
    specific = "observationally_anchored_scenario; 指定主核配对的条件成绩参照。"
    selected = []
    def change(data):
        anchor = next(iter(data["research_v2"]["support_pairs"]))
        support = next(iter(data["research_v2"]["support_pairs"][anchor]))
        prefix = f"research_v2.support_pairs.{anchor}.{support}"
        data["provenance"][prefix] = specific
        selected.append(f"combat.{prefix}.")
    _edit(research_copy, "combat", change)
    records = [record for record in build_parameter_registry(_resolve(research_copy))
               if record.parameter_name.startswith(selected[0])]
    assert records
    assert all(record.source_ref == specific for record in records)
