"""M-CONTRACT：公共类型、时间与运行身份。

本模块只定义跨模块字段语义、DTO 与 Protocol，不实现任何科学算法，
也不持有任何可调数值参数。唯一的常量是契约固定项（单位与判定含义），
改变它们必须升级契约版本，而不是作为普通调参项修改。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

# ---------------------------------------------------------------------------
# 版本与契约固定项
# ---------------------------------------------------------------------------

CONTRACT_VERSION = "1.1.0"
CANONICALIZATION_VERSION = "json_canonical_v1"

#: 契约固定项：JSON 中显式携带用于自描述，检查器要求与此一致。
#: 键为 "<module>.<dotted.path>"，值为契约规定值。
CONTRACT_FIXED_VALUES: Mapping[str, Any] = MappingProxyType(
    {
        "economy.standard_span_days": 42,
        "economy.credit_minor_per_pull": 160,
        "behavior.full_star_threshold": 1,
    }
)

MAX_CINEMA = 6  # 角色影画 0…6 是契约定义的取值域


def contract_value(key: str) -> Any:
    """读取契约固定项（如 standard_span_days）。"""
    return CONTRACT_FIXED_VALUES[key]


# ---------------------------------------------------------------------------
# 不可变容器与 json_canonical_v1 编码
# ---------------------------------------------------------------------------


def deep_freeze(obj: Any) -> Any:
    """递归把 dict→MappingProxyType、list→tuple，得到只读快照。"""
    if isinstance(obj, Mapping):
        return MappingProxyType({k: deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return tuple(deep_freeze(v) for v in obj)
    return obj


def deep_thaw(obj: Any) -> Any:
    """deep_freeze 的逆操作，得到普通 dict/list（新对象）。"""
    if isinstance(obj, Mapping):
        return {k: deep_thaw(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [deep_thaw(v) for v in obj]
    return obj


def _check_encodable(obj: Any, path: str = "$") -> None:
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"{path}: 规范化JSON只允许字符串键，得到 {k!r}")
            _check_encodable(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check_encodable(v, f"{path}[{i}]")
    elif isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"{path}: 禁止 NaN/Infinity")
    elif obj is None or isinstance(obj, (str, int, bool)):
        return
    else:
        raise TypeError(f"{path}: 不可规范化的类型 {type(obj).__name__}")


def canonical_json(obj: Any) -> str:
    """json_canonical_v1 编码：键排序、无多余空白、UTF-8 原样字符、禁止 NaN/Infinity。

    清单排序（按稳定ID）属于配置规范化步骤，由 config 模块在调用前完成；
    此函数不会改变数组顺序。浮点使用 Python 最短往返 repr，版本随 Python 一起锁定。
    """
    _check_encodable(obj)
    return json.dumps(
        deep_thaw(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def sha256_hex(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


# ---------------------------------------------------------------------------
# 校验小工具
# ---------------------------------------------------------------------------


def _require_nonneg_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} 必须是非负整数，得到 {value!r}")


def _require_prob(name: str, value: Optional[float]) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数值概率")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} 必须在 [0,1]，得到 {value!r}")


def _require_finite_nonneg(name: str, value: Optional[float]) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数值")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} 必须是非负有限数，得到 {value!r}")


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class PoolKind(str, Enum):
    CHARACTER = "character"
    WEAPON = "weapon"


class RngType(str, Enum):
    """抽取随机流：S 判定与 UP 判定是独立流。"""

    S = "s_check"
    UP = "up_check"


class EvalStatus(str, Enum):
    OK = "ok"
    NO_FEASIBLE_TEAM = "no_feasible_team"  # 原因已知：能力贡献可定义为 0
    MISSING_MODEL = "missing_model"  # CI 为 NULL
    UNSUPPORTED = "unsupported"  # CI 为 NULL
    ERROR = "error"  # CI 为 NULL

    @property
    def ci_defined(self) -> bool:
        return self in (EvalStatus.OK, EvalStatus.NO_FEASIBLE_TEAM)


class SourceKind(str, Enum):
    SOURCE_CODE = "source_code"
    OBSERVED = "observed"
    COMMUNITY_DERIVED = "community_derived"
    SCENARIO_ASSUMED = "scenario_assumed"


class CreditSource(str, Enum):
    HISTORICAL_FREE = "historical_free"
    CURRENT_FREE = "current_free"
    SUBSCRIPTION = "subscription"
    INSTANT_TOPUP = "instant_topup"


class AssetKind(str, Enum):
    CHARACTER = "character"
    WEAPON = "weapon"
    CATEGORY = "category"  # 非战斗库存类别（如 OFFRATE_S_CHAR），只累计数量


def signature_weapon_id(unit_id: str) -> str:
    """专武资产ID的共用解释规则：规划、抽取、库存与战斗一致使用。"""
    if not unit_id:
        raise ValueError("unit_id 不能为空")
    return f"{unit_id}#signature"


class BundleKind(str, Enum):
    SINGLE_PULL = "single_pull"
    PERIOD = "period"


# ---------------------------------------------------------------------------
# 2.1 时间协议
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeWindow:
    """一个区间的时间字段；有真实日历时另存 ISO 日期和来源时区。"""

    t_idx: int
    period_start_day: float
    period_end_day: float
    evaluation_day: float
    origin_day: float = 0.0
    iso_start: Optional[str] = None
    iso_end: Optional[str] = None
    source_timezone: Optional[str] = None

    def __post_init__(self) -> None:
        if self.period_end_day < self.period_start_day:
            raise ValueError("period_end_day 不能早于 period_start_day")
        if self.evaluation_day < self.origin_day:
            raise ValueError("evaluation_day 不能早于 origin_day")
        if (self.iso_start is None) != (self.iso_end is None):
            raise ValueError("ISO 起止日期必须同时提供或同时缺失")
        if self.iso_start is not None and not self.source_timezone:
            raise ValueError("提供 ISO 日期时必须记录来源时区")

    @property
    def elapsed_days(self) -> float:
        return float(self.period_end_day - self.period_start_day)

    @property
    def delta_span(self) -> float:
        return self.elapsed_days / float(contract_value("economy.standard_span_days"))

    @property
    def cum_ver_span(self) -> float:
        return (self.evaluation_day - self.origin_day) / float(
            contract_value("economy.standard_span_days")
        )


# ---------------------------------------------------------------------------
# 2.2 运行身份
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunIdentity:
    """run_id 对应 (experiment_id, config_id, replicate_id)；attempt_id 只区分重跑。"""

    experiment_id: str
    config_id: str
    replicate_id: str
    master_seed: int
    attempt_id: int = 0

    def __post_init__(self) -> None:
        for name in ("experiment_id", "config_id", "replicate_id"):
            if not getattr(self, name):
                raise ValueError(f"{name} 不能为空")
        _require_nonneg_int("master_seed", self.master_seed)
        _require_nonneg_int("attempt_id", self.attempt_id)

    @property
    def run_id(self) -> str:
        # 种子绑定在 replicate 上，不进入 run_id；attempt 不改变逻辑运行。
        return sha256_hex(
            ["run_id_v1", self.experiment_id, self.config_id, self.replicate_id]
        )


@dataclass(frozen=True)
class ResolvedConfig:
    """解析后的只读科学配置。

    - science：进入 config_id 的规范化科学配置（含算法版本与输入摘要）。
    - metadata：显示名、路径、日志模式、种子、来源说明等不进入科学身份的字段。
    """

    config_id: str
    science: Mapping[str, Any]
    metadata: Mapping[str, Any]
    algorithm_versions: Mapping[str, str]
    input_digests: Mapping[str, str]
    canonicalization_version: str = CANONICALIZATION_VERSION

    def module(self, name: str) -> Mapping[str, Any]:
        """只读模块配置切片。"""
        return self.science["modules"][name]

    def get(self, dotted: str) -> Any:
        """按 "module.a.b" 读取科学参数。"""
        head, *rest = dotted.split(".")
        node: Any = self.module(head)
        for part in rest:
            node = node[part]
        return node


# ---------------------------------------------------------------------------
# 目标规划
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetStage:
    stage_id: str
    component: str  # 如 body / signature_weapon / cinema_1
    pool_kind: PoolKind
    banner_id: str
    skip_if_owned: bool = True


@dataclass(frozen=True)
class TargetPlan:
    """规划与执行共用的固定顺序目标路径。"""

    plan_id: str
    unit_id: str
    stages: tuple[TargetStage, ...]

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("TargetPlan 至少包含一个阶段")
        ids = [s.stage_id for s in self.stages]
        if len(set(ids)) != len(ids):
            raise ValueError("TargetPlan 中 stage_id 必须唯一")


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PityState:
    """gacha 权威；不包含钱包信息。"""

    pity_group: str
    pulls_since_s: int = 0
    guaranteed_up: bool = False

    def __post_init__(self) -> None:
        _require_nonneg_int("pulls_since_s", self.pulls_since_s)


@dataclass(frozen=True)
class CreditBatch:
    batch_id: str
    source: CreditSource
    credit_minor: int
    acquired_day: float

    def __post_init__(self) -> None:
        _require_nonneg_int("credit_minor", self.credit_minor)


@dataclass(frozen=True)
class CashEvent:
    event_id: str
    day: float
    amount_fen: int
    product: str
    credit_minor_granted: int = 0
    related_pull_id: Optional[str] = None

    def __post_init__(self) -> None:
        _require_nonneg_int("amount_fen", self.amount_fen)
        _require_nonneg_int("credit_minor_granted", self.credit_minor_granted)


@dataclass(frozen=True)
class WalletState:
    """wallet 权威：来源批次 credit、现金事件、本期直购额度。"""

    batches: tuple[CreditBatch, ...] = ()
    cash_events: tuple[CashEvent, ...] = ()
    direct_purchase_remaining_pulls: int = 0

    def __post_init__(self) -> None:
        _require_nonneg_int(
            "direct_purchase_remaining_pulls", self.direct_purchase_remaining_pulls
        )

    @property
    def total_credit_minor(self) -> int:
        return sum(b.credit_minor for b in self.batches)


@dataclass(frozen=True)
class BudgetView:
    """只读整数 F/P/B（免费可抽、付费可抽、合计）。"""

    free_pulls: int
    paid_pulls: int

    def __post_init__(self) -> None:
        _require_nonneg_int("free_pulls", self.free_pulls)
        _require_nonneg_int("paid_pulls", self.paid_pulls)

    @property
    def F(self) -> int:  # noqa: N802
        return self.free_pulls

    @property
    def P(self) -> int:  # noqa: N802
        return self.paid_pulls

    @property
    def B(self) -> int:  # noqa: N802
        return self.free_pulls + self.paid_pulls


@dataclass(frozen=True)
class CharacterHolding:
    unit_id: str
    cinema: int
    first_acquired_day: float
    equipped_signature: bool = False
    frozen: bool = False
    gear_value: Optional[float] = None  # combat 计算、roster 保存的实际装备倍率；None=尚未评估

    def __post_init__(self) -> None:
        if self.gear_value is not None:
            _require_finite_nonneg("gear_value", self.gear_value)
        if isinstance(self.cinema, bool) or not isinstance(self.cinema, int):
            raise ValueError("cinema 必须是整数")
        if not 0 <= self.cinema <= MAX_CINEMA:
            raise ValueError(f"cinema 必须在 0…{MAX_CINEMA}")


@dataclass(frozen=True)
class WeaponHolding:
    weapon_id: str
    count: int
    first_acquired_day: float

    def __post_init__(self) -> None:
        _require_nonneg_int("count", self.count)


@dataclass(frozen=True)
class CategoryCount:
    """非战斗库存类别的累计数量（MINOR 新增）。"""

    category_id: str
    count: int

    def __post_init__(self) -> None:
        _require_nonneg_int("count", self.count)


@dataclass(frozen=True)
class RosterState:
    """roster 权威。用元组保存，按稳定ID排序。"""

    characters: tuple[CharacterHolding, ...] = ()
    weapons: tuple[WeaponHolding, ...] = ()
    applied_event_ids: frozenset[str] = frozenset()
    categories: tuple[CategoryCount, ...] = ()  # MINOR 新增
    frozen: bool = False  # MINOR 新增：退出期冻结后不再获取或养成

    def __post_init__(self) -> None:
        cids = [c.unit_id for c in self.characters]
        wids = [w.weapon_id for w in self.weapons]
        kids = [k.category_id for k in self.categories]
        if (len(set(cids)) != len(cids) or len(set(wids)) != len(wids)
                or len(set(kids)) != len(kids)):
            raise ValueError("RosterState 中资产ID必须唯一")
        object.__setattr__(
            self, "categories", tuple(sorted(self.categories, key=lambda k: k.category_id))
        )
        object.__setattr__(
            self, "characters", tuple(sorted(self.characters, key=lambda c: c.unit_id))
        )
        object.__setattr__(
            self, "weapons", tuple(sorted(self.weapons, key=lambda w: w.weapon_id))
        )

    def character(self, unit_id: str) -> Optional[CharacterHolding]:
        return next((c for c in self.characters if c.unit_id == unit_id), None)

    def category_count(self, category_id: str) -> int:
        k = next((k for k in self.categories if k.category_id == category_id), None)
        return 0 if k is None else k.count

    def weapon_count(self, weapon_id: str) -> int:
        w = next((w for w in self.weapons if w.weapon_id == weapon_id), None)
        return 0 if w is None else w.count


RosterView = RosterState  # 只读视图与状态共用不可变类型


# ---------------------------------------------------------------------------
# 战斗
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CombatRequest:
    team: tuple[str, ...]
    anchor_unit_id: str
    investment: Mapping[str, Any]
    actual_g: Optional[float]
    scenario_id: str
    model_version: str
    missing_reason: Optional[str] = None
    # 可替换位为混合型时，它自身的投资与装备值（副输出按其自身强度计算）
    flex_investment: Optional[Mapping[str, Any]] = None
    flex_actual_g: Optional[float] = None


@dataclass(frozen=True)
class CombatResult:
    """CI 为 None 表示未知/不可评估；0 只在 no_feasible_team 等已定义情形出现。"""

    eval_status: EvalStatus
    ci: Optional[float]
    meets_threshold: Optional[bool]
    damage_breakdown: Mapping[str, float]
    config_digest: str
    engine_kind: str = "proxy"

    def __post_init__(self) -> None:
        if self.eval_status.ci_defined:
            if self.ci is None:
                raise ValueError(f"{self.eval_status.value} 必须给出 CI")
            _require_finite_nonneg("ci", self.ci)
            if self.meets_threshold is None:
                raise ValueError("CI 已定义时必须给出 meets_threshold")
        else:
            if self.ci is not None or self.meets_threshold is not None:
                raise ValueError("缺失/unsupported/错误 的 CI 与达标判定必须为 NULL")

    @staticmethod
    def threshold_met(ci: float) -> bool:
        return ci >= contract_value("behavior.full_star_threshold")


# ---------------------------------------------------------------------------
# 规划与行为
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageForecast:
    stage_id: str
    reach_probability: float
    ci: Optional[float]
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    expected_pulls: Optional[float] = None
    expected_direct_pulls: Optional[float] = None

    def __post_init__(self) -> None:
        _require_prob("reach_probability", self.reach_probability)
        for n in ("ci", "ci_low", "ci_high", "expected_pulls", "expected_direct_pulls"):
            _require_finite_nonneg(n, getattr(self, n))


@dataclass(frozen=True)
class Forecast:
    """planner 产物；不含实际未来随机结果。"""

    plan: TargetPlan
    stages: tuple[StageForecast, ...]
    planner_version: str


@dataclass(frozen=True)
class ActionIntent:
    attempt: bool
    plan: Optional[TargetPlan]
    max_spend_pulls: int
    decision_log: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        _require_nonneg_int("max_spend_pulls", self.max_spend_pulls)
        if self.attempt and self.plan is None:
            raise ValueError("attempt=True 时必须给出 TargetPlan")


# ---------------------------------------------------------------------------
# 变更意图与事件
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetDelta:
    """获取/初始化资产；event_id 用于检测重复应用。"""

    event_id: str
    asset_kind: AssetKind
    asset_id: str
    quantity: int
    day: float
    is_initial: bool = False

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("AssetDelta 需要 event_id")
        _require_nonneg_int("quantity", self.quantity)


@dataclass(frozen=True)
class ResourceDelta:
    """带来源批次的 credit 变动；负数表示消耗。"""

    event_id: str
    batch_id: str
    source: CreditSource
    delta_credit_minor: int
    day: float
    related_pull_id: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.delta_credit_minor, bool) or not isinstance(
            self.delta_credit_minor, int
        ):
            raise ValueError("delta_credit_minor 必须是整数")

    @property
    def pulls_equivalent(self) -> float:
        """资源来源消耗的抽数，可为小数。"""
        return self.delta_credit_minor / contract_value("economy.credit_minor_per_pull")


@dataclass(frozen=True)
class PullOutcome:
    pull_id: str
    banner_id: str
    pool_kind: PoolKind
    draw_slot: int
    is_s: bool
    is_up: bool
    result_asset_id: str
    pity_before: PityState
    pity_after: PityState
    asset_deltas: tuple[AssetDelta, ...] = ()

    def __post_init__(self) -> None:
        _require_nonneg_int("draw_slot", self.draw_slot)
        if self.is_up and not self.is_s:
            raise ValueError("UP 结果必须是 S")
        if self.pity_before.pity_group != self.pity_after.pity_group:
            raise ValueError("单抽不能改变 pity_group")


@dataclass(frozen=True)
class LifecycleState:
    active_at_start: bool
    active_at_end: bool
    hoarding: float
    fatigue: float
    failure_span: float
    exit_day: Optional[float] = None
    exit_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.active_at_end and self.exit_day is not None:
            raise ValueError("期末仍活跃时不能有退出时间")
        if (self.exit_day is None) != (self.exit_reason is None):
            raise ValueError("exit_day 与 exit_reason 必须同时出现")


@dataclass(frozen=True)
class EventBundle:
    """同一次单抽或时期的原子提交单元。"""

    bundle_id: str
    kind: BundleKind
    run_id: str
    player_id: int
    t_idx: int
    wallet_before: WalletState
    wallet_after: WalletState
    roster_before: RosterState
    roster_after: RosterState
    pity_before: tuple[PityState, ...] = ()
    pity_after: tuple[PityState, ...] = ()
    pulls: tuple[PullOutcome, ...] = ()
    resource_deltas: tuple[ResourceDelta, ...] = ()
    cash_events: tuple[CashEvent, ...] = ()
    asset_deltas: tuple[AssetDelta, ...] = ()

    def __post_init__(self) -> None:
        if self.kind is BundleKind.SINGLE_PULL:
            if len(self.pulls) != 1:
                raise ValueError("单抽 EventBundle 必须恰好包含一个 PullOutcome")
            pid = self.pulls[0].pull_id
            spent = [d for d in self.resource_deltas if d.delta_credit_minor < 0]
            if not spent and not self.cash_events:
                raise ValueError("单抽必须带有支付记录（不能抽取而不扣款）")
            if any(d.related_pull_id not in (None, pid) for d in self.resource_deltas):
                raise ValueError("资源变动关联了其他抽取")


@dataclass(frozen=True)
class PeriodResult:
    run_id: str
    player_id: int
    window: TimeWindow
    lifecycle: LifecycleState
    pre_eval: Optional[CombatResult]
    post_eval: Optional[CombatResult]
    frozen_eval: Optional[CombatResult]
    bundles: tuple[EventBundle, ...] = ()
    stats: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


# ---------------------------------------------------------------------------
# Protocols（端口）
# ---------------------------------------------------------------------------


@runtime_checkable
class RandomSource(Protocol):
    def uniform(self, event_key: Sequence[Any]) -> float: ...


@runtime_checkable
class CombatEngine(Protocol):
    engine_kind: str

    def evaluate(self, request: CombatRequest) -> CombatResult: ...


@runtime_checkable
class Planner(Protocol):
    planner_version: str

    def forecast_target(
        self, plan: TargetPlan, roster: RosterView, budget: BudgetView, pity: Sequence[PityState]
    ) -> Forecast: ...


@runtime_checkable
class PersistencePort(Protocol):
    def write_bundle(self, bundle: EventBundle) -> None: ...


@runtime_checkable
class ConfigResolver(Protocol):
    def resolve(self) -> ResolvedConfig: ...
