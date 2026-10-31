"""M0：公共契约、配置解析与确定性 RNG。

测试只读取 config/ 下的真实配置；需要变更时复制到 tmp_path 再修改。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import shutil
from pathlib import Path

import pytest

from zzz_research import contracts as C
from zzz_research.config import ConfigError, compute_config_id, normalize, resolve_config
from zzz_research.provenance import (
    FIXED_SOURCE_BASELINE,
    ProvenanceError,
    SourceManifest,
    build_parameter_registry,
    classify_source,
    snapshot_entry,
    uid_link_allowed,
)
from zzz_research.rng import (
    HashCounterParams,
    HashCounterRng,
    RngError,
    churn_key,
    decision_key,
    draw_key,
)

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "config" / "experiment.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def cfg():
    return resolve_config(ENTRY)


@pytest.fixture()
def cfg_copy(tmp_path):
    dst = tmp_path / "config"
    shutil.copytree(ROOT / "config", dst)
    return dst


def _edit(cfg_dir: Path, rel: str, fn) -> None:
    p = cfg_dir / rel
    data = json.loads(p.read_text(encoding="utf-8"))
    fn(data)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _rng(cfg, seed=None):
    seed = cfg.metadata["module_metadata"]["experiments"]["train_seeds"][0] if seed is None else seed
    return HashCounterRng.from_config(seed, cfg.module("randomness"))


# ---------------------------------------------------------------------------
# contracts
# ---------------------------------------------------------------------------


def test_canonical_json_is_sorted_compact_utf8():
    a = C.canonical_json({"b": 1, "a": [1, 2.5], "中": "文"})
    b = C.canonical_json({"中": "文", "a": [1, 2.5], "b": 1})
    assert a == b
    assert " " not in a and "\n" not in a
    assert "中" in a  # 不转义 UTF-8


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_canonical_json_rejects_nonfinite(bad):
    with pytest.raises(ValueError):
        C.canonical_json({"x": bad})


def test_canonical_json_rejects_non_str_keys():
    with pytest.raises(TypeError):
        C.canonical_json({1: "a"})


def test_deep_freeze_is_read_only():
    frozen = C.deep_freeze({"a": [1, {"b": 2}]})
    with pytest.raises(TypeError):
        frozen["a"] = 1  # type: ignore[index]
    assert isinstance(frozen["a"], tuple)
    assert C.deep_thaw(frozen) == {"a": [1, {"b": 2}]}


def test_time_window_spans_use_contract_standard_span(cfg):
    span = cfg.get("scenarios.timeline.days_per_period")
    std = C.contract_value("economy.standard_span_days")
    t = 3
    w = C.TimeWindow(t_idx=t, period_start_day=span * (t - 1), period_end_day=span * t,
                     evaluation_day=span * t)
    assert w.elapsed_days == span
    assert math.isclose(w.delta_span, span / std)
    assert math.isclose(w.cum_ver_span, span * t / std)
    with pytest.raises(ValueError):
        C.TimeWindow(t_idx=1, period_start_day=10, period_end_day=5, evaluation_day=10)
    with pytest.raises(ValueError):
        C.TimeWindow(t_idx=1, period_start_day=0, period_end_day=1, evaluation_day=1,
                     iso_start="2026-01-01", iso_end="2026-01-02")


def test_run_identity():
    r1 = C.RunIdentity("exp", "cfg", "rep1", master_seed=7)
    r2 = C.RunIdentity("exp", "cfg", "rep1", master_seed=7, attempt_id=3)
    r3 = C.RunIdentity("exp", "cfg", "rep2", master_seed=7)
    assert r1.run_id == r2.run_id  # 重跑不是新样本
    assert r1.run_id != r3.run_id
    with pytest.raises(ValueError):
        C.RunIdentity("exp", "cfg", "rep", master_seed=-1)


def test_dtos_are_frozen_and_validate():
    pity = C.PityState("g", 0, False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        pity.pulls_since_s = 1  # type: ignore[misc]
    with pytest.raises(ValueError):
        C.PityState("g", -1)
    with pytest.raises(ValueError):
        C.CharacterHolding("u", cinema=C.MAX_CINEMA + 1, first_acquired_day=0)
    with pytest.raises(ValueError):
        C.WeaponHolding("w", count=-1, first_acquired_day=0)
    with pytest.raises(ValueError):
        C.CreditBatch("b", C.CreditSource.CURRENT_FREE, credit_minor=1.5, acquired_day=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        C.StageForecast("s", reach_probability=1.2, ci=None)
    bv = C.BudgetView(free_pulls=3, paid_pulls=4)
    assert (bv.F, bv.P, bv.B) == (3, 4, 7)


def test_roster_sorted_and_unique():
    r = C.RosterState(characters=(C.CharacterHolding("b", 0, 0), C.CharacterHolding("a", 1, 0)))
    assert [c.unit_id for c in r.characters] == ["a", "b"]
    assert r.character("a").cinema == 1 and r.character("z") is None
    assert r.weapon_count("x") == 0
    with pytest.raises(ValueError):
        C.RosterState(characters=(C.CharacterHolding("a", 0, 0), C.CharacterHolding("a", 1, 0)))


def test_combat_result_null_semantics():
    thr = C.contract_value("behavior.full_star_threshold")
    ok = C.CombatResult(C.EvalStatus.OK, ci=thr, meets_threshold=C.CombatResult.threshold_met(thr),
                        damage_breakdown={}, config_digest="d")
    assert ok.meets_threshold is True
    nf = C.CombatResult(C.EvalStatus.NO_FEASIBLE_TEAM, ci=0.0, meets_threshold=False,
                        damage_breakdown={}, config_digest="d")
    assert nf.ci == 0.0
    for st in (C.EvalStatus.MISSING_MODEL, C.EvalStatus.UNSUPPORTED, C.EvalStatus.ERROR):
        C.CombatResult(st, ci=None, meets_threshold=None, damage_breakdown={}, config_digest="d")
        with pytest.raises(ValueError):  # 错误不能当作失败战力 0
            C.CombatResult(st, ci=0.0, meets_threshold=False, damage_breakdown={}, config_digest="d")
    with pytest.raises(ValueError):
        C.CombatResult(C.EvalStatus.OK, ci=None, meets_threshold=None, damage_breakdown={}, config_digest="d")


def test_target_plan_and_pull_outcome_invariants():
    st = C.TargetStage("s1", "body", C.PoolKind.CHARACTER, "B1")
    with pytest.raises(ValueError):
        C.TargetPlan("p", "u", ())
    with pytest.raises(ValueError):
        C.TargetPlan("p", "u", (st, st))
    p0 = C.PityState("g")
    with pytest.raises(ValueError):
        C.PullOutcome("x", "B1", C.PoolKind.CHARACTER, 0, is_s=False, is_up=True,
                      result_asset_id="a", pity_before=p0, pity_after=p0)
    with pytest.raises(ValueError):
        C.PullOutcome("x", "B1", C.PoolKind.CHARACTER, 0, is_s=False, is_up=False,
                      result_asset_id="a", pity_before=p0, pity_after=C.PityState("other"))


def test_event_bundle_forbids_pull_without_payment(cfg):
    per_pull = cfg.get("economy.credit_minor_per_pull")
    p0 = C.PityState("g")
    pull = C.PullOutcome("pull1", "B1", C.PoolKind.CHARACTER, 0, False, False, "NON_S", p0,
                         C.PityState("g", 1))
    w, r = C.WalletState(), C.RosterState()
    common = dict(bundle_id="b", kind=C.BundleKind.SINGLE_PULL, run_id="r", player_id=1, t_idx=1,
                  wallet_before=w, wallet_after=w, roster_before=r, roster_after=r, pulls=(pull,))
    with pytest.raises(ValueError):
        C.EventBundle(**common)
    pay = C.ResourceDelta("e1", "batch", C.CreditSource.CURRENT_FREE, -per_pull, 42, "pull1")
    assert pay.pulls_equivalent == -1
    C.EventBundle(**common, resource_deltas=(pay,))
    wrong = dataclasses.replace(pay, related_pull_id="pull2")
    with pytest.raises(ValueError):
        C.EventBundle(**common, resource_deltas=(wrong,))


def test_lifecycle_and_action_intent():
    with pytest.raises(ValueError):
        C.LifecycleState(True, True, 0, 0, 0, exit_day=3, exit_reason="churn")
    with pytest.raises(ValueError):
        C.LifecycleState(True, False, 0, 0, 0, exit_day=3)
    with pytest.raises(ValueError):
        C.ActionIntent(attempt=True, plan=None, max_spend_pulls=0)


def test_protocols_runtime_checkable(cfg):
    assert isinstance(_rng(cfg), C.RandomSource)

    class Sink:
        def write_bundle(self, bundle):
            pass

    assert isinstance(Sink(), C.PersistencePort)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_resolve_real_config(cfg):
    assert len(cfg.config_id) == 64
    assert set(cfg.science["modules"]) == set(json.loads(ENTRY.read_text("utf-8"))["modules"])
    assert cfg.algorithm_versions["randomness.rng_version"] == "hash_counter_v1"
    assert cfg.algorithm_versions["config.canonicalization"] == C.CANONICALIZATION_VERSION
    raw_gacha = json.loads((ROOT / "config/modules/gacha.json").read_text("utf-8"))
    assert cfg.get("gacha.pools.character.hard_pity") == raw_gacha["pools"]["character"]["hard_pity"]
    with pytest.raises(TypeError):
        cfg.module("gacha")["pools"] = {}  # 只读


def test_config_id_is_deterministic(cfg):
    assert resolve_config(ENTRY).config_id == cfg.config_id
    assert compute_config_id(C.deep_thaw(cfg.science)) == cfg.config_id


def test_config_id_independent_of_key_order_and_whitespace(cfg_copy, cfg):
    p = cfg_copy / "modules/gacha.json"
    data = json.loads(p.read_text("utf-8"))
    reordered = dict(reversed(list(data.items())))
    p.write_text(json.dumps(reordered, ensure_ascii=False, indent=7), encoding="utf-8")
    assert resolve_config(cfg_copy / "experiment.json").config_id == cfg.config_id


def test_config_id_independent_of_stable_list_order(cfg_copy, cfg):
    _edit(cfg_copy, "modules/population.json", lambda d: d["tiers"].reverse())
    _edit(cfg_copy, "modules/scenarios.json", lambda d: d["events"].reverse())
    assert resolve_config(cfg_copy / "experiment.json").config_id == cfg.config_id


def test_ordered_lists_are_not_sorted(cfg_copy, cfg):
    _edit(cfg_copy, "modules/economy.json", lambda d: d["spend_priority"].reverse())
    assert resolve_config(cfg_copy / "experiment.json").config_id != cfg.config_id


@pytest.mark.parametrize(
    "rel,mutate",
    [
        ("experiment.json", lambda d: d["execution"].__setitem__("log_mode", "summary")),
        ("experiment.json", lambda d: d["execution"].__setitem__("output_root", "elsewhere")),
        ("experiment.json", lambda d: d.__setitem__("description", "changed")),
        ("experiment.json", lambda d: d.__setitem__("profile_id", "renamed")),
        ("modules/experiments.json", lambda d: d["train_seeds"].append(max(d["train_seeds"]) + 1)),
        ("modules/experiments.json", lambda d: d.__setitem__("grid_log_mode", "full")),
        ("modules/combat.json", lambda d: d.__setitem__("description", "x")),
        ("modules/scenarios.json", lambda d: d["timeline"].__setitem__("label_prefix", "Q")),
    ],
)
def test_non_science_changes_keep_config_id(cfg_copy, cfg, rel, mutate):
    _edit(cfg_copy, rel, mutate)
    new = resolve_config(cfg_copy / "experiment.json")
    assert new.config_id == cfg.config_id


@pytest.mark.parametrize(
    "rel,mutate",
    [
        ("modules/gacha.json", lambda d: d["pools"]["character"].__setitem__(
            "hard_pity", d["pools"]["character"]["hard_pity"] + 1)),
        ("modules/behavior.json", lambda d: d["fatigue"].__setitem__(
            "gap_weight", d["fatigue"]["gap_weight"] * 2)),
        ("modules/combat.json", lambda d: d.__setitem__("model_version", d["model_version"] + "_x")),
        ("modules/randomness.json", lambda d: d.__setitem__("domain_tag", d["domain_tag"] + "_x")),
        ("experiment.json", lambda d: d.__setitem__("mode", d["mode"] + "_x")),
    ],
)
def test_science_changes_change_config_id(cfg_copy, cfg, rel, mutate):
    _edit(cfg_copy, rel, mutate)
    assert resolve_config(cfg_copy / "experiment.json").config_id != cfg.config_id


def test_integral_float_normalized(cfg_copy, cfg):
    def to_float(d):
        d["utility"]["beta_gain"] = float(d["utility"]["beta_gain"])
    _edit(cfg_copy, "modules/behavior.json", to_float)
    assert resolve_config(cfg_copy / "experiment.json").config_id == cfg.config_id
    # 整数字段仍拒绝浮点
    _edit(cfg_copy, "modules/population.json", lambda d: d.__setitem__("n_players", float(d["n_players"])))
    with pytest.raises(ConfigError):
        resolve_config(cfg_copy / "experiment.json")
    assert normalize([1.0, 2.5]) == [1, 2.5]


def test_overrides(cfg):
    path = "behavior.fatigue.budget_weight"
    base = cfg.get(path)
    o = resolve_config(ENTRY, overrides={path: base * 2})
    assert o.get(path) == base * 2 and o.config_id != cfg.config_id
    assert resolve_config(ENTRY, overrides={path: base}).config_id == cfg.config_id
    with pytest.raises(ConfigError):
        resolve_config(ENTRY, overrides={"behavior.fatigue.nope": 1})
    with pytest.raises(ConfigError):
        resolve_config(ENTRY, overrides={path: "text"})
    with pytest.raises(ConfigError):
        resolve_config(ENTRY, overrides={path: float("nan")})


def _write_raw(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


def test_duplicate_key_rejected(cfg_copy):
    p = cfg_copy / "modules/randomness.json"
    text = p.read_text("utf-8").replace('"digest"', '"digest": "SHA256", "digest"', 1)
    _write_raw(p, text)
    with pytest.raises(ConfigError, match="重复键"):
        resolve_config(cfg_copy / "experiment.json")


def test_nan_rejected(cfg_copy):
    p = cfg_copy / "modules/behavior.json"
    data = p.read_text("utf-8")
    import re
    _write_raw(p, re.sub(r'"temperature":\s*[0-9.eE+-]+', '"temperature": NaN', data))
    with pytest.raises(ConfigError, match="NaN"):
        resolve_config(cfg_copy / "experiment.json")


@pytest.mark.parametrize(
    "rel,mutate,msg",
    [
        ("modules/gacha.json", lambda d: d.__setitem__("surprise", 1), "未知字段"),
        ("modules/gacha.json", lambda d: d.pop("pools"), "缺失字段"),
        ("modules/gacha.json", lambda d: d["pools"]["weapon"].__setitem__("up_given_s", 1.5), "非法概率"),
        ("modules/behavior.json", lambda d: d["tier_policy"].pop(sorted(d["tier_policy"])[0]), "缺失 Tier"),
        ("modules/behavior.json", lambda d: d.__setitem__("support_target", "nope"), "目标路径"),
        ("modules/population.json", lambda d: d["tiers"][0].__setitem__("share", 0.0), "share"),
        ("modules/economy.json", lambda d: d.__setitem__(
            "credit_minor_per_pull", d["credit_minor_per_pull"] + 1), "契约固定项"),
        ("modules/economy.json", lambda d: d.__setitem__(
            "standard_span_days", d["standard_span_days"] + 1), "契约固定项"),
        ("modules/combat.json", lambda d: d["support"]["gains"].__setitem__("GHOST", 0.1), "不存在的商品"),
        ("modules/randomness.json", lambda d: d.__setitem__("strategy_id_in_key", True), "公共随机键"),
        ("modules/randomness.json", lambda d: d.__setitem__("schema_version", "2.0.0"), "schema_version"),
        ("modules/experiments.json", lambda d: d.__setitem__("base_scenario", "Nope"), "情景"),
        ("experiment.json", lambda d: d.__setitem__("extra", 1), "未知字段"),
        ("experiment.json", lambda d: d["modules"].__setitem__("combat", "../../outside.json"), "越出配置根"),
    ],
)
def test_invalid_configs_fail(cfg_copy, rel, mutate, msg):
    _edit(cfg_copy, rel, mutate)
    with pytest.raises(ConfigError, match=msg):
        resolve_config(cfg_copy / "experiment.json")


# ---------------------------------------------------------------------------
# rng
# ---------------------------------------------------------------------------


def test_rng_reference_vector_matches_spec(cfg):
    """按设计文字独立复算：SHA256(UTF-8 canonical key) 前 N 字节大端 → 右移 → 除以 2**bits。"""
    rcfg = cfg.module("randomness")
    seed = cfg.metadata["module_metadata"]["experiments"]["train_seeds"][0]
    rng = HashCounterRng.from_config(seed, rcfg)
    key = draw_key(17, 3, "SYN_P03_A", C.PoolKind.CHARACTER, 5, C.RngType.S)
    payload = json.dumps([rcfg["domain_tag"], [seed, *key]], separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    d = hashlib.new(rcfg["digest"].lower(), payload).digest()
    x = int.from_bytes(d[: rcfg["uint_bytes"]], rcfg["byte_order"])
    expected = (x >> rcfg["discard_low_bits"]) / (1 << rcfg["uniform_bits"])
    assert rng.uniform(key) == expected


def test_rng_deterministic_and_seed_sensitive(cfg):
    a, b = _rng(cfg), _rng(cfg)
    k = churn_key(1, 2)
    assert a.uniform(k) == b.uniform(k)
    seeds = cfg.metadata["module_metadata"]["experiments"]["train_seeds"]
    other = HashCounterRng.from_config(seeds[1], cfg.module("randomness"))
    assert other.uniform(k) != a.uniform(k)


def test_rng_streams_are_distinct(cfg):
    rng = _rng(cfg)
    s = rng.draw_u(1, 1, "B", "character", 0, "s_check")
    u = rng.draw_u(1, 1, "B", "character", 0, "up_check")
    w = rng.draw_u(1, 1, "B", "weapon", 0, "s_check")
    nxt = rng.draw_u(1, 1, "B", "character", 1, "s_check")
    assert len({s, u, w, nxt}) == 4
    assert rng.decision_u(1, 1, "B", 0) != rng.churn_u(1, 1)
    assert rng.population_u(1, "tier") != rng.population_u(2, "tier")


def test_rng_range_and_uniformity(cfg):
    rng = _rng(cfg)
    us = rng.block(decision_key(0, 0, "B", 0), 0, 20000)
    assert all(0.0 <= u < 1.0 for u in us)
    mean = sum(us) / len(us)
    assert abs(mean - 0.5) < 0.01
    frac_low = sum(u < 0.25 for u in us) / len(us)
    assert abs(frac_low - 0.25) < 0.015


def test_rng_hit_boundaries(cfg):
    rng = _rng(cfg)
    keys = [churn_key(i, 1) for i in range(500)]
    assert all(rng.hit(k, 1.0) for k in keys)
    assert not any(rng.hit(k, 0.0) for k in keys)
    with pytest.raises(RngError):
        rng.hit(keys[0], 1.5)


def test_rng_block_equals_single_calls_and_order_independent(cfg):
    rng = _rng(cfg)
    prefix = [5, 2, "SYN_P02_A", "character"]
    blk = rng.block(prefix, 10, 50)
    assert blk == [rng.uniform([*prefix, s]) for s in range(10, 60)]
    # 不同块切分与逆序计算结果一致
    assert rng.block(prefix, 10, 20) + rng.block(prefix, 30, 30) == blk
    assert [rng.uniform([*prefix, s]) for s in reversed(range(10, 60))] == blk[::-1]


def test_rng_rejects_bad_keys_and_params(cfg):
    rng = _rng(cfg)
    for bad in ([], "abc", [1.5], [True], [""], [None]):
        with pytest.raises(RngError):
            rng.uniform(bad)
    with pytest.raises(RngError):
        HashCounterRng(-1, HashCounterParams.from_config(cfg.module("randomness")))
    bad = dict(C.deep_thaw(cfg.module("randomness")))
    bad["discard_low_bits"] += 1
    with pytest.raises(RngError):
        HashCounterParams.from_config(bad)
    bad = dict(C.deep_thaw(cfg.module("randomness")))
    bad["rng_version"] = "other"
    with pytest.raises(RngError):
        HashCounterParams.from_config(bad)


def test_rng_key_does_not_depend_on_config_id(cfg):
    rng1 = _rng(cfg)
    o = resolve_config(ENTRY, overrides={"behavior.temperature": cfg.get("behavior.temperature") * 3})
    assert o.config_id != cfg.config_id
    rng2 = _rng(o)
    k = draw_key(9, 4, "SYN_P04_A", "weapon", 2, "up_check")
    assert rng1.uniform(k) == rng2.uniform(k)


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


def test_source_manifest_append_only(tmp_path):
    f = tmp_path / "snap.csv"
    f.write_bytes(b"a,b\n1,2\n")
    e1 = snapshot_entry("SRC_SHIYU", f, coverage="test", fetched_at="2026-01-01T00:00:00+00:00")
    assert e1.sha256 == hashlib.sha256(b"a,b\n1,2\n").hexdigest() and e1.size_bytes == 8
    assert e1.revision == FIXED_SOURCE_BASELINE["SRC_SHIYU"].revision
    m = SourceManifest()
    m.add(e1)
    m.add(e1)  # 幂等
    with pytest.raises(ProvenanceError):
        m.add(dataclasses.replace(e1, coverage="other"))
    f.write_bytes(b"a,b\n1,3\n")
    e2 = snapshot_entry("SRC_SHIYU", f, coverage="test", fetched_at="2026-02-01T00:00:00+00:00")
    m.add(e2)
    assert [e.snapshot_id for e in m.snapshots("SRC_SHIYU")] == [e1.snapshot_id, e2.snapshot_id]
    with pytest.raises(ProvenanceError):
        dataclasses.replace(e1, sha256=None)


def test_uid_link_boundary():
    assert uid_link_allowed("snap_A", "snap_A")
    assert not uid_link_allowed("snap_A", "snap_B")


def test_classify_source():
    assert classify_source("SRC_ZSIM")[0] is C.SourceKind.SOURCE_CODE
    assert classify_source("scenario_assumed")[0] is C.SourceKind.SCENARIO_ASSUMED
    assert classify_source("community_derived")[0] is C.SourceKind.COMMUNITY_DERIVED
    assert classify_source("observed_shiyu")[0] is C.SourceKind.OBSERVED


def test_parameter_registry(cfg):
    reg = build_parameter_registry(cfg)
    names = [r.parameter_name for r in reg]
    assert len(names) == len(set(names))
    by = {r.parameter_name: r for r in reg}
    lvl = by["combat.reference.attacker_level_coefficient"]
    assert lvl.source_kind is C.SourceKind.SOURCE_CODE and lvl.source_ref == "SRC_ZSIM"
    assert json.loads(lvl.value_json) == cfg.get("combat.reference.attacker_level_coefficient")
    assert by["gacha.pools.character.hard_pity"].source_kind is C.SourceKind.SCENARIO_ASSUMED
    for p in cfg.module("experiments")["pain_sensitivity"]["parameters"]:
        assert "pain_sensitivity" in by[p].sensitivity_set
    assert not any(n.endswith("provenance") or "train_seeds" in n for n in names)
