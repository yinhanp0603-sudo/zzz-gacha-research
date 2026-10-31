"""M-CONFIG：配置装配、json_canonical_v1 规范化与 config_id 计算。

所有数值参数只来自 config/experiment.json 及其引用的模块 JSON；
本模块只包含结构规则（字段集合、归属、排序键、科学/非科学划分），不含任何数值默认值。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import platform
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .contracts import (
    CANONICALIZATION_VERSION,
    CONTRACT_FIXED_VALUES,
    CONTRACT_VERSION,
    ResolvedConfig,
    canonical_json,
    deep_freeze,
)
from .rng import HashCounterParams, RngError
from .agents.spending import ChannelParams, SpendingParams
from .combat.proxy import ProxyParams
from .versions import scientific_algorithm_versions


class ConfigError(ValueError):
    """配置不合法：重复键、未知字段、NaN、非法概率、缺失引用等。"""


SUPPORTED_SCHEMA_MAJOR = "1"

# ---------------------------------------------------------------------------
# 结构规则
# ---------------------------------------------------------------------------

ENTRY_KEYS = frozenset({"schema_version", "profile_id", "mode", "modules", "execution", "description"})
ENTRY_REQUIRED = frozenset({"schema_version", "mode", "modules"})
EXECUTION_KEYS = frozenset({"log_mode", "output_root", "allow_network_during_run"})

#: 每个模块允许（且要求）的顶层字段；"description" 可选。
MODULE_KEYS: Mapping[str, frozenset[str]] = {
    "combat": frozenset({
        "schema_version", "model_version", "reference", "gear_growth", "investment",
        "support", "shield", "default_penetration", "default_defense_reduction", "provenance",
    }),
    "gacha": frozenset({
        "schema_version", "model_version", "curve_version", "pools", "target_paths",
        "offrate_combat_enabled", "non_s_result", "provenance",
    }),
    "economy": frozenset({
        "schema_version", "model_version", "credit_minor_per_pull", "standard_span_days",
        "free_pulls_per_standard_span", "subscription", "topup", "spend_priority",
        "allow_mixed_existing_provenance_payment", "paid_budget_rollover", "provenance",
    }),
    "population": frozenset({
        "schema_version", "model_version", "n_players", "tiers", "core_retention_tier",
        "allow_new_players", "allow_return", "provenance",
    }),
    "behavior": frozenset({
        "schema_version", "model_version", "comfort_threshold", "full_star_threshold",
        "temperature", "utility", "tier_policy", "support_target", "hoarding", "fatigue",
        "churn", "initial", "provenance",
    }),
    "scenarios": frozenset({
        "schema_version", "model_version", "timeline", "initial_assets", "product_power",
        "events", "reference_scenarios", "provenance",
    }),
    "randomness": frozenset({
        "schema_version", "rng_version", "domain_tag", "digest", "uint_bytes", "byte_order",
        "discard_low_bits", "uniform_bits", "interval", "strategy_id_in_key",
        "config_id_in_key", "event_key_version", "overflow_policy",
    }),
    "experiments": frozenset({
        "schema_version", "protocol_version", "base_scenario", "grid", "train_seeds",
        "validation_seeds", "pain_sensitivity", "baseline_grid_point", "grid_log_mode",
        "reference_log_mode",
    }),
}
OPTIONAL_MODULE_KEYS = frozenset({"description"})
MODULE_EXTENSION_KEYS: Mapping[str, frozenset[str]] = {
    name: frozenset({"research_v2"}) for name in ("combat", "behavior", "scenarios")
}

#: 任意层级出现即视为元数据的键（来源说明、显示文本、格式版本）。
METADATA_KEYS_ANYWHERE = frozenset({"description", "provenance", "schema_version"})

#: 不进入科学身份的模块字段（种子、日志模式、显示标签）。
METADATA_PATHS: Mapping[str, tuple[tuple[str, ...], ...]] = {
    "scenarios": (("timeline", "label_prefix"),),
    "experiments": (("train_seeds",), ("validation_seeds",), ("grid_log_mode",), ("reference_log_mode",)),
}

#: 清单稳定排序键：若数组元素全为含这些键的对象，按该键元组排序。
STABLE_LIST_KEYS: tuple[tuple[str, ...], ...] = (
    ("id",),
    ("t", "event_order", "banner_id"),
)

VERSION_KEY_SUFFIX = "_version"


# ---------------------------------------------------------------------------
# 严格 JSON 读取
# ---------------------------------------------------------------------------


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ConfigError(f"重复键: {k!r}")
        out[k] = v
    return out


def _reject_constant(name: str) -> Any:
    raise ConfigError(f"禁止 NaN/Infinity: {name}")


def load_json_strict(path: Path) -> Any:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"找不到配置文件: {path}") from exc
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: JSON 解析失败: {exc}") from exc


# ---------------------------------------------------------------------------
# json_canonical_v1 规范化
# ---------------------------------------------------------------------------


def _stable_sort_key(items: list[Any]) -> Optional[tuple[str, ...]]:
    if not items or not all(isinstance(x, dict) for x in items):
        return None
    for keys in STABLE_LIST_KEYS:
        if all(all(k in x for k in keys) for x in items):
            return keys
    return None


def normalize(obj: Any) -> Any:
    """展开为可规范化形式：稳定ID排序清单、整数值浮点归一为整数、检查有限性。"""
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        items = [normalize(v) for v in obj]
        keys = _stable_sort_key(items)
        if keys is not None:
            sort_vals = [tuple(x[k] for k in keys) for x in items]
            if len(set(map(repr, sort_vals))) != len(sort_vals):
                raise ConfigError(f"清单稳定ID重复: {keys}")
            items = sorted(items, key=lambda x: tuple((type(x[k]).__name__, x[k]) for k in keys))
        return items
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ConfigError("禁止 NaN/Infinity")
        if obj.is_integer():
            return int(obj)
        return obj
    return obj


def compute_config_id(science: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(normalize(dict(science))).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _get_path(node: Any, parts: Iterable[str]) -> Any:
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            raise KeyError(p)
        node = node[p]
    return node


def _split_metadata(module: dict[str, Any], name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """把模块拆为 (科学部分, 元数据部分)。"""
    science = copy.deepcopy(module)
    meta: dict[str, Any] = {}

    def strip(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for k in sorted(node):
                path = f"{prefix}.{k}" if prefix else k
                if k in METADATA_KEYS_ANYWHERE:
                    meta[path] = node.pop(k)
                else:
                    strip(node[k], path)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                strip(v, f"{prefix}[{i}]")

    for parts in METADATA_PATHS.get(name, ()):
        parent = science
        try:
            parent = _get_path(science, parts[:-1])
        except KeyError:
            continue
        if isinstance(parent, dict) and parts[-1] in parent:
            meta[".".join(parts)] = parent.pop(parts[-1])
    strip(science, "")
    return science, meta


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _check_prob(where: str, x: Any) -> None:
    if not _is_number(x) or not math.isfinite(x) or not 0 <= x <= 1:
        raise ConfigError(f"{where}: 非法概率 {x!r}")


def _check_pos_int(where: str, x: Any) -> None:
    if isinstance(x, bool) or not isinstance(x, int) or x <= 0:
        raise ConfigError(f"{where}: 需要正整数，得到 {x!r}")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _check_schema_version(where: str, v: Any) -> None:
    if not isinstance(v, str) or v.split(".")[0] != SUPPORTED_SCHEMA_MAJOR:
        raise ConfigError(f"{where}: 不支持的 schema_version {v!r}")


def _validate_keys(name: str, mod: Mapping[str, Any]) -> None:
    allowed = MODULE_KEYS[name] | OPTIONAL_MODULE_KEYS | MODULE_EXTENSION_KEYS.get(name, frozenset())
    unknown = set(mod) - allowed
    missing = MODULE_KEYS[name] - set(mod)
    if unknown:
        raise ConfigError(f"{name}: 未知字段 {sorted(unknown)}")
    if missing:
        raise ConfigError(f"{name}: 缺失字段 {sorted(missing)}")


def _validate_contract_fixed(modules: Mapping[str, Mapping[str, Any]]) -> None:
    for key, expected in CONTRACT_FIXED_VALUES.items():
        mod, *parts = key.split(".")
        try:
            actual = _get_path(modules[mod], parts)
        except KeyError as exc:
            raise ConfigError(f"缺失契约固定项 {key}") from exc
        if actual != expected or isinstance(actual, bool):
            raise ConfigError(f"契约固定项 {key} 必须为 {expected!r}，得到 {actual!r}（改变须升级契约）")


def _validate_research_modules(m: Mapping[str, Mapping[str, Any]]) -> None:
    combat, behavior, scenarios = m["combat"], m["behavior"], m["scenarios"]
    combat_active = str(combat["model_version"]).startswith("proxy_v2")
    behavior_active = str(behavior["model_version"]).startswith("behavior_v2")
    scenario_active = str(scenarios["model_version"]).startswith("scenario_v2")
    research_combat = combat.get("research_v2")
    if combat_active and research_combat is None:
        raise ConfigError("combat: proxy_v2 需要 research_v2")
    if research_combat is not None:
        try:
            ProxyParams.from_config(combat)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ConfigError(f"combat.research_v2: {exc}") from exc
        main_ids = {"starter_dps"} | {
            e["unit_id"] for e in scenarios["events"] if e["role"] != "support"}
        support_ids = {e["unit_id"] for e in scenarios["events"] if e["role"] in ("support", "hybrid")}
        profile_ids = set(research_combat["unit_profiles"])
        unknown = profile_ids - main_ids
        missing = main_ids - profile_ids
        if unknown:
            raise ConfigError(f"combat.research_v2.unit_profiles 引用了未知主核: {sorted(unknown)}")
        if combat_active and missing:
            raise ConfigError(f"combat.research_v2.unit_profiles 缺失主核: {sorted(missing)}")
        pair_supports = {support for pairs in research_combat["support_pairs"].values() for support in pairs}
        if pair_supports - support_ids:
            raise ConfigError("combat.research_v2.support_pairs 必须引用 role=support 或 hybrid 的商品")
        hybrid_ids = {e["unit_id"] for e in scenarios["events"] if e["role"] == "hybrid"}
        if set(research_combat.get("co_output", {})) - hybrid_ids:
            raise ConfigError("combat.research_v2.co_output 只能登记 role=hybrid 的商品")

    if behavior_active:
        research = behavior.get("research_v2")
        if not isinstance(research, Mapping) or set(research) - {"novelty"} != {"spending"}:
            raise ConfigError("behavior_v2.research_v2 必须包含 spending，可选 novelty")
        novelty = research.get("novelty", {"beta_novelty": 0})
        if (not isinstance(novelty, Mapping) or set(novelty) != {"beta_novelty"}
                or not _is_number(novelty["beta_novelty"]) or novelty["beta_novelty"] < 0):
            raise ConfigError("behavior.research_v2.novelty 只含非负 beta_novelty")
        spending = research["spending"]
        if not isinstance(spending, Mapping) or set(spending) != {"subscription", "topup"}:
            raise ConfigError("behavior.research_v2.spending 需要 subscription 与 topup")
        channel_keys = set(ChannelParams.__dataclass_fields__)
        for channel in ("subscription", "topup"):
            required = channel_keys | ({"gain_weight", "cost_weight"} if channel == "topup" else set())
            values = spending[channel]
            if not isinstance(values, Mapping) or set(values) != required:
                raise ConfigError(f"behavior.research_v2.spending.{channel} 字段必须为 {sorted(required)}")
        try:
            SpendingParams.from_config(behavior)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ConfigError(f"behavior.research_v2: {exc}") from exc

    research_scenarios = scenarios.get("research_v2")
    if scenario_active and research_scenarios is None:
        raise ConfigError("scenarios: scenario_v2 需要 research_v2")
    if research_scenarios is not None:
        if not isinstance(research_scenarios, Mapping) or set(research_scenarios) != {
                "observation_periods", "mechanism_schedule"}:
            raise ConfigError("scenarios.research_v2 需要 observation_periods 与 mechanism_schedule")
        n = scenarios["timeline"]["n_periods"]
        observed = research_scenarios["observation_periods"]
        _check_pos_int("scenarios.research_v2.observation_periods", observed)
        if observed > n:
            raise ConfigError("scenarios.research_v2.observation_periods 不能超过时间轴")
        schedule = research_scenarios["mechanism_schedule"]
        if not isinstance(schedule, Mapping) or set(schedule) != {str(t) for t in range(1, n + 1)}:
            raise ConfigError("scenarios.research_v2.mechanism_schedule 必须覆盖时间轴每一期")
        kinds = set(research_combat["mechanisms"]) if combat_active else {"defense", "window", "barrier"}
        if any(not isinstance(kind, str) or kind not in kinds for kind in schedule.values()):
            raise ConfigError("scenarios.research_v2.mechanism_schedule 引用了未登记机制")
    elif combat_active:
        raise ConfigError("proxy_v2 需要 scenarios.research_v2.mechanism_schedule")


def _validate_semantics(m: Mapping[str, Mapping[str, Any]]) -> None:
    gacha, pop, beh, scen, rng, exp = (
        m["gacha"], m["population"], m["behavior"], m["scenarios"], m["randomness"], m["experiments"],
    )

    # gacha
    groups = set()
    for pk, pool in gacha["pools"].items():
        w = f"gacha.pools.{pk}"
        for f in ("base_s_probability", "soft_increment", "up_given_s"):
            _check_prob(f"{w}.{f}", pool[f])
        _check_pos_int(f"{w}.hard_pity", pool["hard_pity"])
        _check_pos_int(f"{w}.soft_pity_start", pool["soft_pity_start"])
        if pool["soft_pity_start"] > pool["hard_pity"]:
            raise ConfigError(f"{w}: soft_pity_start 不能大于 hard_pity")
        if not pool.get("pity_group"):
            raise ConfigError(f"{w}: 缺失 pity_group")
        groups.add(pool["pity_group"])
    if len(groups) != len(gacha["pools"]):
        raise ConfigError("gacha: 不同卡池类型的 pity_group 必须唯一")
    for pid, path in gacha["target_paths"].items():
        if not path or len(set(path)) != len(path):
            raise ConfigError(f"gacha.target_paths.{pid}: 阶段必须非空且唯一")

    # population
    tier_ids = [t["id"] for t in pop["tiers"]]
    if len(set(tier_ids)) != len(tier_ids):
        raise ConfigError("population.tiers: id 重复")
    for t in pop["tiers"]:
        _check_prob(f"population.tiers.{t['id']}.share", t["share"])
    if not math.isclose(sum(t["share"] for t in pop["tiers"]), 1.0, abs_tol=1e-9):
        raise ConfigError("population.tiers: share 之和必须为 1")
    if pop["core_retention_tier"] not in tier_ids:
        raise ConfigError("population.core_retention_tier 引用了不存在的 Tier")
    _check_pos_int("population.n_players", pop["n_players"])

    # behavior
    missing = sorted(set(tier_ids) - set(beh["tier_policy"]))
    extra = sorted(set(beh["tier_policy"]) - set(tier_ids))
    if missing:
        raise ConfigError(f"behavior.tier_policy 缺失 Tier 策略: {missing}")
    if extra:
        raise ConfigError(f"behavior.tier_policy 引用了未知 Tier: {extra}")
    for tid, pol in beh["tier_policy"].items():
        if pol["dps_target"] not in gacha["target_paths"]:
            raise ConfigError(f"behavior.tier_policy.{tid}.dps_target 引用未知目标路径")
    if beh["support_target"] not in gacha["target_paths"]:
        raise ConfigError("behavior.support_target 引用未知目标路径")
    _check_prob("behavior.comfort_threshold", beh["comfort_threshold"])
    _check_prob("behavior.churn.base_hazard", beh["churn"]["base_hazard"])

    # scenarios
    n_periods = scen["timeline"]["n_periods"]
    _check_pos_int("scenarios.timeline.n_periods", n_periods)
    banners = [e["banner_id"] for e in scen["events"]]
    units = [e["unit_id"] for e in scen["events"]]
    if len(set(banners)) != len(banners):
        raise ConfigError("scenarios.events: banner_id 必须唯一")
    if len(set(units)) != len(units):
        raise ConfigError("scenarios.events: unit_id 必须唯一")
    for e in scen["events"]:
        if not (isinstance(e["t"], int) and 1 <= e["t"] <= n_periods):
            raise ConfigError(f"scenarios.events.{e['unit_id']}: t 超出时间轴")
        _check_prob(f"scenarios.events.{e['unit_id']}.uptime", e["uptime"])
        if e["role"] in ("support", "hybrid") and e["unit_id"] not in m["combat"]["support"]["gains"]:
            raise ConfigError(f"支援角色 {e['unit_id']} 缺失 combat.support.gains 商品引用")
    for g in m["combat"]["support"]["gains"]:
        if g not in units:
            raise ConfigError(f"combat.support.gains.{g} 引用了不存在的商品")
    if exp["base_scenario"] not in scen["reference_scenarios"]:
        raise ConfigError("experiments.base_scenario 引用未知情景")

    _validate_research_modules(m)

    # randomness
    try:
        HashCounterParams.from_config(rng)
    except RngError as exc:
        raise ConfigError(f"randomness: {exc}") from exc
    if rng["strategy_id_in_key"] or rng["config_id_in_key"]:
        raise ConfigError("randomness: strategy_id/config_id 不得进入公共随机键")

    # experiments
    for p in exp["pain_sensitivity"]["parameters"]:
        mod, *parts = p.split(".")
        try:
            v = _get_path(m[mod], parts)
        except KeyError as exc:
            raise ConfigError(f"experiments.pain_sensitivity 引用未知参数 {p}") from exc
        if not _is_number(v):
            raise ConfigError(f"experiments.pain_sensitivity 参数 {p} 不是数值")
    for axis in exp["baseline_grid_point"]:
        if axis not in exp["grid"]:
            raise ConfigError(f"experiments.baseline_grid_point 引用未知轴 {axis}")


# ---------------------------------------------------------------------------
# 覆盖
# ---------------------------------------------------------------------------


def _apply_overrides(modules: dict[str, dict[str, Any]], overrides: Mapping[str, Any]) -> None:
    for dotted in sorted(overrides):
        value = overrides[dotted]
        mod, *parts = dotted.split(".")
        if mod not in modules or not parts:
            raise ConfigError(f"覆盖字段必须形如 module.path: {dotted}")
        try:
            parent = _get_path(modules[mod], parts[:-1])
            old = parent[parts[-1]] if isinstance(parent, dict) else None
        except KeyError as exc:
            raise ConfigError(f"覆盖字段不存在: {dotted}") from exc
        if not isinstance(parent, dict) or parts[-1] not in parent:
            raise ConfigError(f"覆盖字段不存在: {dotted}")
        if _is_number(old) != _is_number(value) or isinstance(old, bool) != isinstance(value, bool):
            raise ConfigError(f"覆盖字段类型不匹配: {dotted}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ConfigError(f"覆盖值禁止 NaN/Infinity: {dotted}")
        parent[parts[-1]] = copy.deepcopy(value)


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------


def _collect_versions(modules: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    out = {"config.canonicalization": CANONICALIZATION_VERSION, "contract": CONTRACT_VERSION}
    out.update(scientific_algorithm_versions())
    for name in sorted(modules):
        for k, v in modules[name].items():
            if k.endswith(VERSION_KEY_SUFFIX) and k != "schema_version":
                out[f"{name}.{k}"] = v
    return out


def resolve_config(entry_path: Path | str, overrides: Optional[Mapping[str, Any]] = None) -> ResolvedConfig:
    """读取入口与全部模块 → 校验 → 应用显式覆盖 → 规范化 → 计算 config_id。"""
    entry_path = Path(entry_path)
    root = entry_path.resolve().parent
    entry = load_json_strict(entry_path)
    if not isinstance(entry, dict):
        raise ConfigError("入口必须是 JSON 对象")
    unknown = set(entry) - ENTRY_KEYS
    if unknown:
        raise ConfigError(f"入口未知字段 {sorted(unknown)}")
    if ENTRY_REQUIRED - set(entry):
        raise ConfigError(f"入口缺失字段 {sorted(ENTRY_REQUIRED - set(entry))}")
    _check_schema_version("experiment", entry["schema_version"])
    if set(entry.get("execution", {})) - EXECUTION_KEYS:
        raise ConfigError("execution 含未知字段")
    if set(entry["modules"]) != set(MODULE_KEYS):
        raise ConfigError(f"modules 必须恰好包含 {sorted(MODULE_KEYS)}")

    raw_modules: dict[str, dict[str, Any]] = {}
    file_digests: dict[str, str] = {}
    for name in sorted(entry["modules"]):
        rel = entry["modules"][name]
        p = (root / rel).resolve()
        if not p.is_relative_to(root):
            raise ConfigError(f"模块路径越出配置根: {rel}")
        mod = load_json_strict(p)
        if not isinstance(mod, dict):
            raise ConfigError(f"{name}: 模块必须是 JSON 对象")
        _check_schema_version(name, mod.get("schema_version"))
        _validate_keys(name, mod)
        raw_modules[name] = mod
        file_digests[name] = hashlib.sha256(p.read_bytes()).hexdigest()

    if overrides:
        _apply_overrides(raw_modules, overrides)

    _validate_contract_fixed(raw_modules)
    _validate_semantics(raw_modules)

    sci_modules: dict[str, Any] = {}
    meta_modules: dict[str, Any] = {}
    for name in sorted(raw_modules):
        sci, meta = _split_metadata(raw_modules[name], name)
        sci_modules[name] = normalize(sci)
        meta_modules[name] = meta

    versions = _collect_versions(raw_modules)
    input_digests = {
        name: hashlib.sha256(canonical_json(sci_modules[name]).encode("utf-8")).hexdigest()
        for name in sorted(sci_modules)
    }
    science = {
        "canonicalization_version": CANONICALIZATION_VERSION,
        "contract_version": CONTRACT_VERSION,
        "mode": entry["mode"],
        "algorithm_versions": versions,
        "input_digests": input_digests,
        "modules": sci_modules,
    }
    config_id = compute_config_id(science)
    metadata = {
        "entry_path": str(entry_path),
        "profile_id": entry.get("profile_id"),
        "description": entry.get("description"),
        "execution": entry.get("execution", {}),
        "module_paths": dict(entry["modules"]),
        "module_file_sha256": file_digests,
        "module_metadata": meta_modules,
        "overrides": dict(overrides or {}),
        "python_version": platform.python_version(),
        "float_repr": "python_shortest_roundtrip",
    }
    return ResolvedConfig(
        config_id=config_id,
        science=deep_freeze(science),
        metadata=deep_freeze(metadata),
        algorithm_versions=deep_freeze(versions),
        input_digests=deep_freeze(input_digests),
    )


def config_slice(cfg: ResolvedConfig, module: str) -> Mapping[str, Any]:
    """只读模块切片；计算模块只接收切片，不 import 全局可变字典。"""
    return cfg.module(module)
