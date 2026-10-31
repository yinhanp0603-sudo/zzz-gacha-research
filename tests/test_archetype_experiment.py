"""体系化合成世界：混合型同时可作主C与辅助位，配对由体系成员关系生成，辅助按主核搭配率分类。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from zzz_research import contracts as C
from zzz_research.agents import roster as R
from zzz_research.combat.proxy import ProxyCombatModel
from zzz_research.config import ConfigError, resolve_config
from zzz_research.simulation import resolve_world

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_support_archetypes import categorize  # noqa: E402
from run_archetype_experiment import cells, world, write_cell_config  # noqa: E402


def _cell(cell_id):
    return next(c for c in cells() if c["cell_id"] == cell_id)


def _world(tmp_path, cell_id):
    cfg = resolve_config(write_cell_config(tmp_path, _cell(cell_id)))
    return cfg, resolve_world(cfg, ProxyCombatModel(cfg.module("combat")))


def test_hybrid_is_both_anchor_and_flex_but_never_its_own_support(tmp_path):
    cfg, w = _world(tmp_path, "A_hybrid_fit_L1")
    assert "H07" in w.anchors and "H07" in w.supports
    roster = R.initial_roster(cfg.module("scenarios"), player_id=0)
    for unit in ("L03", "H07", "SUP_L04"):
        roster = R.apply_asset_delta(roster, C.AssetDelta(unit, C.AssetKind.CHARACTER, unit, 1, 1))
    teams = R.enumerate_feasible_teams(roster, anchors=w.anchors, neutral_fixed=w.neutral_fixed,
                                       neutral_flex=w.neutral_flex, supports=w.supports)
    assert ("L03", w.neutral_fixed, "H07") in teams
    assert ("H07", w.neutral_fixed, "SUP_L04") in teams
    assert all(not (t[0] == "H07" and t[2] == "H07") for t in teams)


def test_existing_research_world_is_unchanged_by_hybrid_support():
    cfg = resolve_config(ROOT / "config/research_v2/experiment.json")
    w = resolve_world(cfg, ProxyCombatModel(cfg.module("combat")))
    assert w.supports == ("S_A", "S_B", "S_C", "S_D")
    assert all(not u.startswith("S_") for u in w.anchors)


def test_pairs_follow_archetype_membership_and_cells():
    _, narrow = world(_cell("A_narrow_fit_L1"))
    _, system = world(_cell("A_system_fit_L1"))
    _, hybrid = world(_cell("A_hybrid_unfit_L2"))
    with_p07 = lambda combat: {m for m, p in combat["research_v2"]["support_pairs"].items() if "P07" in p}
    assert with_p07(narrow) == {"L03"}
    assert with_p07(system) == {"L01", "L03", "L05", "L09", "L11", "L13", "L18"}
    assert not with_p07(hybrid)
    assert {m for m, p in hybrid["research_v2"]["support_pairs"].items() if "H07" in p} == with_p07(system)
    assert hybrid["research_v2"]["mechanism_strength"] == 2.0
    scenarios, combat = world(_cell("B_lock_yes"))
    assert [scenarios["research_v2"]["mechanism_schedule"][str(t)] for t in (13, 14, 16, 17)] == \
        ["defense", "barrier", "barrier", "defense"]
    assert {m for m, p in combat["research_v2"]["support_pairs"].items() if "P15" in p} == with_p07(system)


def test_support_pairs_must_reference_support_or_hybrid(tmp_path):
    path = write_cell_config(tmp_path, _cell("A_system_fit_L1"))
    combat = path.parent / "modules/combat.json"
    data = json.loads(combat.read_text("utf-8"))
    data["research_v2"]["support_pairs"]["L03"]["L01"] = {"net_gain": .2, "match_lift": {}}
    combat.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    with pytest.raises(ConfigError, match="未登记为支援资产|role=support 或 hybrid"):
        resolve_config(path)


def _attach(core, group, rate):
    return {"core": core, "core_group": group, "attach_rate": rate}


def test_categories_use_strong_cores_and_family_groups():
    assert categorize([_attach("A", "命破贯穿", .9)])[0] == "单核专拐"
    assert categorize([_attach("A", "命破贯穿", .9), _attach("B", "命破贯穿", .5)])[0] == "体系专拐"
    assert categorize([_attach("A", "命破贯穿", .9), _attach("B", "异常/紊乱异放", .5)])[0] == "跨体系"
    many = [_attach(str(i), "常规直伤/失衡" if i % 2 else "追加攻击", .4) for i in range(8)]
    assert categorize(many)[0] == "泛用"
    nine_same = [_attach(str(i), "异常/紊乱异放", .6) for i in range(9)]
    assert categorize(nine_same)[0] == "体系专拐"
    assert categorize([_attach("A", "锐化防御", .27), _attach("B", "常规直伤/失衡", .16)])[0] == "未定型"


def _request(model, w, team, t=8, flex=None):
    inv = {"B_ref": w.units[team[0]].b_ref, "pierce_share": 0.0, "cinema": 0, "signature_weapon": False}
    flex_inv = flex and {"B_ref": w.units[flex].b_ref, "pierce_share": 0.0, "cinema": 0, "signature_weapon": False}
    return C.CombatRequest(team=team, anchor_unit_id=team[0], investment=inv, actual_g=1.0,
                           scenario_id=w.periods[t - 1].scenario.scenario_id, model_version=model.params.model_version,
                           flex_investment=flex_inv, flex_actual_g=1.0 if flex else None)


def test_co_output_adds_hybrid_solo_damage_only_in_flex_slot(tmp_path):
    results = {}
    for cell_id in ("A_hybrid_fit_L1", "A_hybrid_r50_fit_L1"):
        cfg, w = _world(tmp_path / cell_id, cell_id)
        model = ProxyCombatModel(cfg.module("combat"))
        resolve_world(cfg, model)
        results[cell_id] = {
            "team": model.evaluate(_request(model, w, ("L03", w.neutral_fixed, "H07"), flex="H07")).ci,
            "solo": model.evaluate(_request(model, w, ("H07", w.neutral_fixed, w.neutral_flex))).ci,
            "anchor": model.evaluate(_request(model, w, ("H07", w.neutral_fixed, "SUP_L04"))).ci,
        }
    plain, co = results["A_hybrid_fit_L1"], results["A_hybrid_r50_fit_L1"]
    assert co["team"] == pytest.approx(plain["team"] + 0.5 * plain["solo"])
    assert (co["solo"], co["anchor"]) == pytest.approx((plain["solo"], plain["anchor"]))


def test_co_output_needs_flex_investment_and_hybrid_role(tmp_path):
    cfg, w = _world(tmp_path, "A_hybrid_r25_fit_L1")
    model = ProxyCombatModel(cfg.module("combat"))
    resolve_world(cfg, model)
    with pytest.raises(ValueError, match="flex_investment"):
        model.evaluate(_request(model, w, ("L03", w.neutral_fixed, "H07")))
    path = write_cell_config(tmp_path / "bad", _cell("A_hybrid_r25_fit_L1"))
    combat = path.parent / "modules/combat.json"
    data = json.loads(combat.read_text("utf-8"))
    data["research_v2"]["co_output"] = {"L03": .25}
    combat.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    with pytest.raises(ConfigError, match="co_output|副输出"):
        resolve_config(path)
