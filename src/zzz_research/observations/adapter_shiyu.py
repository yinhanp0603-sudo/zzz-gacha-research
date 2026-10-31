"""M-OBS 3.1：ShiyuStats 公开快照 → 规范化观测表（obs_*）。

字段映射与有效通关规则逐条对应固定修订 d930cec5 的源码（见 SOURCE_RULES 中的文件引用）；
原始字段只在本适配器内转换。不推定跨期同一玩家，不推定付费身份。
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from ..provenance import FIXED_SOURCE_BASELINE, sha256_file

SOURCE_ID = "SRC_SHIYU"
SOURCE_REVISION = FIXED_SOURCE_BASELINE[SOURCE_ID].revision
ADAPTER_VERSION = "shiyu_adapter_v3"

MODE_SD = "shiyu_defense"
MODE_DA = "deadly_assault"
REGIME_TIME = "time"
REGIME_SCORE = "score"

#: 源码中的模式/评分制度规则（scripts/comp_rates_config.py 与 composition.py @ SOURCE_REVISION）。
#: 这些是来源事实而非模型参数：改变时须升级 ADAPTER_VERSION。
SOURCE_RULES: Mapping[str, Any] = {
    "ref": f"scripts/comp_rates_config.py@{SOURCE_REVISION}",
    MODE_SD: {
        "rating_to_star": {"B": 1, "A": 2, "S": 3},
        "default": {"stages": ("7-1", "7-2"), "star_threshold": 3, "regime": REGIME_TIME},
        "changes": ((date(2025, 12, 30), {"stages": ("5-1", "5-2", "5-3"), "star_threshold": 3,
                                          "regime": REGIME_SCORE}),),
    },
    MODE_DA: {
        "default": {"stages": ("1-1", "1-2", "1-3"), "star_threshold": 3, "regime": REGIME_SCORE},
        "changes": ((date(2026, 7, 29), {"stages": ("1-1", "1-2", "1-3", "2-1"), "star_threshold": 3,
                                         "regime": REGIME_SCORE}),),
        # composition.py: valid_clear = star>=threshold or (da and stage==2 and star>=1)
        "stage2_min_star": 1,
        # csv_to_pickle.py: floor>3 → stage 2 node 1，否则 stage 1 node=floor
        "floor_split": 3,
    },
}

EXCL_DUPLICATE = "duplicate_uid_block"
EXCL_EMPTY = "empty_team"
EXCL_NOT_VALID = "not_valid_clear"
EXCL_STAGE = "stage_not_in_regime"
EXCL_UNPARSEABLE = "unparseable_row"
EXCL_MISSING_UID = "missing_uid"


class ObservationError(ValueError):
    pass


# --------------------------------------------------------------------------- 版本映射


@dataclass(frozen=True)
class CollectionPeriod:
    collection_period: str
    collect_date: date
    sd_season: Optional[str]
    da_season: Optional[str]

    def season(self, mode: str) -> Optional[str]:
        return self.sd_season if mode == MODE_SD else self.da_season


def _ddmmyyyy(s: str) -> date:
    return datetime.strptime(s, "%d/%m/%Y").date()


def load_version_map(path: Path | str) -> dict[str, CollectionPeriod]:
    """采集期 → 战斗期映射（data/versions/config.json）。采集期不直接当战斗版本。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for key, v in raw.items():
        out[key] = CollectionPeriod(
            key, _ddmmyyyy(v["collect_date"]),
            (v.get("sd") or {}).get("ver"), (v.get("da") or {}).get("ver"))
    return out


def regime_for(mode: str, collect_date: date) -> dict:
    rules = SOURCE_RULES[mode]
    cur = dict(rules["default"])
    for since, change in rules["changes"]:  # 源码按时间顺序首个满足者生效
        if collect_date >= since:
            cur = dict(change)
            break
    return cur


def load_characters(path: Path | str) -> dict[str, dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- ID 与登记


def source_file_id(path: Path | str, digest: Optional[str] = None) -> str:
    d = digest or sha256_file(path)[0]
    return f"{SOURCE_ID}:{Path(path).name}@{d[:16]}"


def _hid(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode("utf-8")).hexdigest()[:24]


def snapshot_group_id(base_snapshot_id: str, collection_period: str) -> str:
    """UID 只在同一采集期内稳定；同一期战斗/角色文件共享身份组。"""
    if not base_snapshot_id or not collection_period:
        raise ObservationError("快照 ID 与采集期不能为空")
    return f"{base_snapshot_id}:{collection_period}"


def manifest_row(path: Path | str, *, url: str, revision: str, coverage: Mapping[str, Any],
                 retrieved_at: str, status: str = "fetched", data_license: Optional[str] = None) -> dict:
    """原始 CSV 的许可须由数据快照声明；仅仓库元数据可默认沿用代码许可。"""
    digest, size = sha256_file(path)
    license_value = data_license
    if license_value is None and coverage.get("kind") == "metadata":
        license_value = FIXED_SOURCE_BASELINE[SOURCE_ID].license
    return {"source_file_id": source_file_id(path, digest), "source_id": SOURCE_ID, "url": url,
            "revision": revision, "retrieved_at": retrieved_at, "sha256": digest, "byte_size": size,
            "license": license_value,
            "coverage_json": json.dumps(dict(coverage), ensure_ascii=False, sort_keys=True), "status": status}


# --------------------------------------------------------------------------- 解析


@dataclass(frozen=True)
class ParsedSnapshot:
    battles: list[dict]
    members: list[dict]
    builds: list[dict]
    manifest: list[dict]


def _read_csv(path: Path) -> Iterator[tuple[int, dict]]:
    # 先以固定大小块验证完整编码，再逐行解析。不能在解码过程中先 yield：
    # 若文件后部才出现非 UTF-8 字节，回退 cp1252 会重复产出前部记录。
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=enc) as f:
                while f.read(1024 * 1024):
                    pass
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise ObservationError(f"无法解码 {path}")
    with open(path, newline="", encoding=enc) as f:
        for i, r in enumerate(csv.DictReader(f), start=2):  # 保持原先含表头的记录序号
            yield i, r


def parse_builds(path: Path, *, snapshot_id: str, sfid: str) -> list[dict]:
    """{ver}_char.csv：每 uid 每角色一行的持有/配装展示。缺失≠未持有。"""
    out, seen = [], set()
    for line_no, r in _read_csv(path):
        uid, name = (r.get("uid") or "").strip(), (r.get("name") or "").strip()
        if not uid or not name:
            continue
        bid = _hid(sfid, uid, name)
        if bid in seen:  # 源码：同一 uid 重复块跳过
            continue
        seen.add(bid)
        build = {k: r.get(k) for k in ("level", "cons", "weapon", "element", "artifacts")}
        out.append({"build_record_id": bid, "source_file_id": sfid, "snapshot_id": snapshot_id,
                    "local_uid": uid, "unit_id": name,
                    "build_json": json.dumps(build, ensure_ascii=False, sort_keys=True),
                    "coverage_status": "known"})
    return out


def _known_cinema(value: Any) -> Optional[int]:
    """接受上游整型/浮点文本（如 '2.0'）；不截断小数，不将非法值当 0。

    Decimal 保留文本精度，避免 '1.0000000000000001' 先转 float 后被舍入成整数。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or not 0 <= number <= 6:
        return None
    if number != number.to_integral_value():
        return None
    return int(number)


def parse_battles(path: Path, *, mode: str, snapshot_id: str, sfid: str, period: CollectionPeriod,
                  builds_by_uid_unit: Mapping[tuple[str, str], dict]) -> tuple[list[dict], list[dict]]:
    """{ver}.csv（式舆防卫战）或 {ver}_da.csv（危局强袭战）。"""
    regime = regime_for(mode, period.collect_date)
    rules = SOURCE_RULES[mode]
    season = period.season(mode)
    battles, members = [], []
    seen_uids: set[str] = set()
    last_uid, skip_uid = None, False
    for line_no, r in _read_csv(path):
        uid = (r.get("uid") or "").strip()
        bid = _hid(sfid, line_no)
        reason: Optional[str] = EXCL_MISSING_UID if not uid else None
        # 源码去重：uid 在其他 uid 之后再次出现的整块跳过
        if uid != last_uid:
            skip_uid = uid in seen_uids
            seen_uids.add(uid)
        last_uid = uid
        try:
            if mode == MODE_DA:
                star = int(r["star"])
                floor = int(r["floor"])
                stage, node = (2, 1) if floor > rules["floor_split"] else (1, floor)
            else:
                star = rules["rating_to_star"][r["rating"].strip()]
                stage, node = int(r["floor"]), int(r["node"])
            raw_val = r.get("score") if r.get("score") not in (None, "") else r.get("time")
            value = float(raw_val) if raw_val not in (None, "") else None
        except (KeyError, ValueError):
            star, stage, node, value = None, None, None, None
            reason = reason or EXCL_UNPARSEABLE
        chars = [(i, (r.get(f"ch{i}") or "").strip()) for i in range(1, 4)]
        chars = [(i, c) for i, c in chars if c]
        stage_id = None if stage is None else f"{stage}-{node}"
        valid = None
        if reason is None:
            valid = star >= regime["star_threshold"] or (
                mode == MODE_DA and stage == 2 and star >= rules["stage2_min_star"])
            if skip_uid:
                reason = EXCL_DUPLICATE
            elif not chars:
                reason = EXCL_EMPTY
            elif not valid:
                reason = EXCL_NOT_VALID
            elif stage_id not in regime["stages"]:
                reason = EXCL_STAGE
        battles.append({
            "battle_id": bid, "source_file_id": sfid, "source_row_locator": f"{path.name}:{line_no}",
            "snapshot_id": snapshot_id, "local_uid": uid or None, "collection_period": period.collection_period,
            "combat_season": season, "mode": mode, "stage_id": stage_id, "scoring_regime": regime["regime"],
            "score_value": value if regime["regime"] == REGIME_SCORE else None,
            "clear_time_seconds": value if regime["regime"] == REGIME_TIME else None,
            "valid_clear": None if valid is None else int(valid),
            "is_eligible": int(reason is None), "exclusion_reason": reason})
        for slot, unit in chars:
            rank = r.get(f"ch{slot}_rank")
            b = builds_by_uid_unit.get((uid, unit))
            known_cinema: Optional[int] = None
            if rank not in (None, ""):
                known_cinema = _known_cinema(rank)
            elif b is not None:
                cons = json.loads(b["build_json"]).get("cons")
                known_cinema = _known_cinema(cons)
            weapon = json.loads(b["build_json"]).get("weapon") if b else None
            members.append({"battle_id": bid, "team_slot": slot, "unit_id": unit, "known_cinema": known_cinema,
                            "known_weapon": weapon or None,
                            "build_record_id": b["build_record_id"] if b else None,
                            "build_missing_reason": None if b else "no_char_record"})
    return battles, members


def discover(snapshot_dir: Path | str, version_map: Mapping[str, CollectionPeriod]) -> list[tuple[str, str, Path, Optional[Path]]]:
    """返回 [(collection_period, mode, battle_csv, char_csv)]；未登记的采集期报错而不是猜测。"""
    d = Path(snapshot_dir)
    out = []
    for p in sorted(d.glob("*.csv")):
        stem = p.stem
        if stem.endswith(("_char", "_build")):
            continue
        mode = MODE_DA if stem.endswith("_da") else MODE_SD
        ver = stem[: -len("_da")] if mode == MODE_DA else stem
        if ver not in version_map:
            raise ObservationError(f"{p.name} 的采集期 {ver} 不在版本映射中")
        char = d / f"{ver}_char.csv"
        out.append((ver, mode, p, char if char.exists() else None))
    return out


def iter_snapshot_batches(snapshot_dir: Path | str, *, version_map_path: Path | str, snapshot_id: str,
                          url: str, revision: str, retrieved_at: str,
                          data_license: Optional[str] = None) -> Iterator[ParsedSnapshot]:
    """每个战斗文件产出一批，同采集期角色表及其清单只在首次批次产出。

    仅缓存当前采集期的角色资料；消费方应逐批写入并释放，不将全部批次存入列表。
    仍按 discover 的原文件顺序解析，不改变去重、有效通关或源行定位规则。
    没有战斗文件的目录拒绝导入，避免空快照被误记成已取得真实观测。
    """
    vm = load_version_map(version_map_path)
    files = discover(snapshot_dir, vm)
    if not files:
        raise ObservationError(f"快照目录没有可导入的战斗 CSV: {snapshot_dir}")
    current_period: Optional[str] = None
    cached_builds: list[dict] = []
    idx: dict[tuple[str, str], dict] = {}
    cached_sfid: Optional[str] = None
    emitted_chars: set[Path] = set()
    for ver, mode, battle_csv, char_csv in files:
        group_id = snapshot_group_id(snapshot_id, ver)
        coverage_base = {"collection_period": ver, "base_snapshot_id": snapshot_id,
                         "snapshot_group_id": group_id, "adapter_version": ADAPTER_VERSION}
        builds, manifest = [], []
        if ver != current_period:
            # 先释放上一期缓存；后续身份组不能沿用旧期相同 UID 的资料。
            current_period, cached_builds, cached_sfid, idx = ver, [], None, {}
            if char_csv is not None:
                cached_sfid = source_file_id(char_csv)
                cached_builds = parse_builds(char_csv, snapshot_id=group_id, sfid=cached_sfid)
                idx = {(b["local_uid"], b["unit_id"]): b for b in cached_builds}
        if char_csv is not None and char_csv not in emitted_chars:
            builds = cached_builds
            manifest.append(manifest_row(char_csv, url=url, revision=revision, retrieved_at=retrieved_at,
                                         coverage={**coverage_base, "kind": "char"},
                                         data_license=data_license))
            emitted_chars.add(char_csv)
        sfid = source_file_id(battle_csv)
        manifest.append(manifest_row(battle_csv, url=url, revision=revision, retrieved_at=retrieved_at,
                                     coverage={**coverage_base, "mode": mode, "kind": "battle"},
                                     data_license=data_license))
        battles, members = parse_battles(battle_csv, mode=mode, snapshot_id=group_id, sfid=sfid, period=vm[ver],
                                         builds_by_uid_unit=idx)
        yield ParsedSnapshot(battles, members, builds, manifest)
        del battles, members, builds, manifest


def parse_snapshot(snapshot_dir: Path | str, *, version_map_path: Path | str, snapshot_id: str,
                   url: str, revision: str, retrieved_at: str,
                   data_license: Optional[str] = None) -> ParsedSnapshot:
    """兼容完整聚合 API；全历史导入应改用 iter_snapshot_batches 逐批消费。"""
    battles, members, builds, manifest = [], [], [], []
    for batch in iter_snapshot_batches(snapshot_dir, version_map_path=version_map_path, snapshot_id=snapshot_id,
                                       url=url, revision=revision, retrieved_at=retrieved_at,
                                       data_license=data_license):
        battles.extend(batch.battles)
        members.extend(batch.members)
        builds.extend(batch.builds)
        manifest.extend(batch.manifest)
    return ParsedSnapshot(battles, members, builds, manifest)


def write_snapshot(writer: Any, parsed: ParsedSnapshot) -> None:
    """写入 obs_* 与 source_manifest（同一 source_file_id 已存在则跳过）。"""
    writer.write_dim("source_manifest", parsed.manifest)
    with writer.atomic_buffer():
        writer.write_rows("obs_build", parsed.builds)
        writer.write_rows("obs_battle", parsed.battles)
        writer.write_rows("obs_team_member", parsed.members)
    writer.flush()

