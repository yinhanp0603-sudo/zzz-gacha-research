"""M-SOURCE：外部来源、快照与参数登记。

- source_manifest：URL、修订、获取时间、SHA256、大小、许可、覆盖、状态、验证说明。
- parameter_registry：每个科学参数的值、来源类型、来源引用、适用范围与状态。
原始快照只读；外部更新建立新快照，不覆盖旧记录。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Optional

from .contracts import ResolvedConfig, SourceKind, canonical_json, deep_thaw


class ProvenanceError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 已检查的固定源码基线（来源登记数据，不是数值参数）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceBaseline:
    source_id: str
    url: str
    revision: Optional[str]
    usage: str
    license: Optional[str]


FIXED_SOURCE_BASELINE: Mapping[str, SourceBaseline] = MappingProxyType({
    "SRC_SHIYU": SourceBaseline(
        "SRC_SHIYU", "https://github.com/LvlUrArti/ShiyuStats",
        "d930cec50bfde460d1a6ebd9acef8b240a676000", "数据口径与公开快照入口", "MIT",
    ),
    "SRC_ZSIM": SourceBaseline(
        "SRC_ZSIM", "https://github.com/ZSim-Dev/ZSim",
        "e248e9f149a6b889290579d8e673e132be9bde31", "防御公式与可选完整引擎", "GPL-3.0",
    ),
    "SRC_SHIELD": SourceBaseline(
        "SRC_SHIELD", "https://news.17173.com/content/07112025/114804086.shtml",
        None, "社区条件推导的来源线索；非官方参数或独立实测", None,
    ),
    "SRC_SURVIVAL": SourceBaseline(
        "SRC_SURVIVAL",
        "https://lifelines.readthedocs.io/en/latest/Time%20varying%20survival%20regression.html",
        None, "可选生存分析的数据格式与解释边界", None,
    ),
})

FETCH_STATUSES = frozenset({"fetched", "pending", "failed", "not_fetched"})


# ---------------------------------------------------------------------------
# source_manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceManifestEntry:
    source_id: str
    snapshot_id: str
    url: str
    revision: Optional[str]
    fetched_at: str  # ISO 8601 UTC
    sha256: Optional[str]
    size_bytes: Optional[int]
    license: Optional[str]
    coverage: str
    fetch_status: str
    verification_note: str = ""

    def __post_init__(self) -> None:
        if self.fetch_status not in FETCH_STATUSES:
            raise ProvenanceError(f"未知 fetch_status {self.fetch_status!r}")
        if self.fetch_status == "fetched" and (self.sha256 is None or self.size_bytes is None):
            raise ProvenanceError("已获取快照必须记录 sha256 与大小")


def sha256_file(path: Path | str, chunk: int = 1 << 20) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
            size += len(b)
    return h.hexdigest(), size


def snapshot_entry(
    source_id: str, path: Path | str, *, url: Optional[str] = None,
    revision: Optional[str] = None, license: Optional[str] = None,
    coverage: str = "", fetched_at: Optional[str] = None, verification_note: str = "",
) -> SourceManifestEntry:
    """为一个本地只读快照文件生成清单条目；snapshot_id 由内容摘要决定。"""
    base = FIXED_SOURCE_BASELINE.get(source_id)
    digest, size = sha256_file(path)
    return SourceManifestEntry(
        source_id=source_id,
        snapshot_id=f"{source_id}@{digest[:16]}",
        url=url or (base.url if base else ""),
        revision=revision if revision is not None else (base.revision if base else None),
        fetched_at=fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        sha256=digest,
        size_bytes=size,
        license=license if license is not None else (base.license if base else None),
        coverage=coverage,
        fetch_status="fetched",
        verification_note=verification_note,
    )


@dataclass
class SourceManifest:
    """仅追加：同一 snapshot_id 不能被不同内容覆盖；外部更新形成新快照。"""

    _entries: dict[str, SourceManifestEntry] = field(default_factory=dict)

    def add(self, entry: SourceManifestEntry) -> None:
        old = self._entries.get(entry.snapshot_id)
        if old is not None:
            if old != entry:
                raise ProvenanceError(f"快照 {entry.snapshot_id} 已存在，禁止覆盖")
            return
        self._entries[entry.snapshot_id] = entry

    def snapshots(self, source_id: str) -> list[SourceManifestEntry]:
        return sorted(
            (e for e in self._entries.values() if e.source_id == source_id),
            key=lambda e: (e.fetched_at, e.snapshot_id),
        )

    def __iter__(self) -> Iterator[SourceManifestEntry]:
        return iter(sorted(self._entries.values(), key=lambda e: e.snapshot_id))

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# ShiyuStats 可用边界
# ---------------------------------------------------------------------------


def uid_link_allowed(snapshot_group_a: str, snapshot_group_b: str) -> bool:
    """公开 UID 按采集期重新编号：仅同一快照组内可关联，不跨期推定同一玩家。"""
    return snapshot_group_a == snapshot_group_b


# ---------------------------------------------------------------------------
# parameter_registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParameterRecord:
    parameter_name: str
    value_json: str
    unit: Optional[str]
    source_kind: SourceKind
    source_ref: str
    applicability: str
    rationale: str
    sensitivity_set: tuple[str, ...]
    status: str


def classify_source(label: str) -> tuple[SourceKind, str]:
    """把配置里的 provenance 标签映射为 (来源类型, 来源引用)。"""
    if label in FIXED_SOURCE_BASELINE:
        base = FIXED_SOURCE_BASELINE[label]
        kind = SourceKind.SOURCE_CODE if base.revision else SourceKind.COMMUNITY_DERIVED
        return kind, label
    low = label.lower()
    if low.startswith("observed"):
        return SourceKind.OBSERVED, label
    if "assum" in low:  # 社区启发但仍属情景假设
        return SourceKind.SCENARIO_ASSUMED, label
    if "community" in low:
        return SourceKind.COMMUNITY_DERIVED, label
    return SourceKind.SCENARIO_ASSUMED, label


def _pick_provenance(prov: Any, path: str) -> str:
    """模块 provenance 为字符串时整体适用；为对象时按路径首段与词重叠选择最具体的键。"""
    if isinstance(prov, str):
        return prov
    if not isinstance(prov, Mapping) or not prov:
        return "scenario_assumed"
    matched = [k for k in prov if path == k or path.startswith(k + ".")]
    if matched:
        return str(prov[max(matched, key=len)])
    segs = path.split(".")
    tokens = set("_".join(segs).split("_"))
    best: Optional[tuple[int, str]] = None
    for key in sorted(prov):
        ktoks = key.split("_")
        if ktoks[0] != segs[0]:
            continue
        score = len(tokens & set(ktoks))
        if best is None or score > best[0]:
            best = (score, key)
    if best is not None:
        return str(prov[best[1]])
    fallback = next((k for k in sorted(prov) if k.startswith("other")), None)
    return str(prov[fallback]) if fallback else "scenario_assumed"


def _leaves(node: Any, prefix: str) -> Iterator[tuple[str, Any]]:
    if isinstance(node, Mapping):
        for k in sorted(node):
            yield from _leaves(node[k], f"{prefix}.{k}" if prefix else k)
    else:
        yield prefix, node


def build_parameter_registry(cfg: ResolvedConfig) -> list[ParameterRecord]:
    """遍历科学配置的每个参数（数组视为一个值），生成参数登记。"""
    sens = set()
    exp = cfg.module("experiments")
    for p in exp["pain_sensitivity"]["parameters"]:
        sens.add(p)
    grid_axes = set(exp["grid"])

    records: list[ParameterRecord] = []
    meta = cfg.metadata["module_metadata"]
    for mod in sorted(cfg.science["modules"]):
        prov = deep_thaw(meta.get(mod, {}).get("provenance", "scenario_assumed"))
        for path, value in _leaves(cfg.module(mod), ""):
            name = f"{mod}.{path}"
            kind, ref = classify_source(_pick_provenance(prov, path))
            sset = []
            if name in sens:
                sset.append("pain_sensitivity")
            if path.split(".")[-1] in grid_axes:
                sset.append("search_grid")
            records.append(ParameterRecord(
                parameter_name=name,
                value_json=canonical_json(deep_thaw(value)),
                unit=None,
                source_kind=kind,
                source_ref=ref,
                applicability=cfg.science["mode"],
                rationale="",
                sensitivity_set=tuple(sset),
                status="active",
            ))
    return records
