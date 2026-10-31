"""M-SEARCH 12.1–12.4：25 点候选、筛选/复核、非支配集合、秩相关与痛感扰动。

- 每个逻辑 run 写独立 SQLite（outputs/<experiment>/runs/<run_id>.sqlite），主进程校验后合并到 search.sqlite。
- 相同 (config_id, seed) 只运行一次并在映射中复用；计时与独立样本数按去重后的 run 计算。
- 候选与扰动只通过显式 overrides 形成新的科学配置；不复用其他配置的抽取与流失决定。
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ..config import compute_config_id, resolve_config
from ..contracts import ResolvedConfig, RunIdentity, deep_thaw, sha256_hex
from ..simulation import run_simulation
from ..storage.writer import SQLiteWriter
from .cohorts import run_objectives

SEARCH_PROTOCOL = "search_v1"
ANALYTICS_SCHEMA = Path(__file__).resolve().parent.parent / "storage" / "analytics_schema.sql"
SPLIT_TRAIN, SPLIT_VALID = "train", "validation"
P0 = "P0"

#: λ 轴打包作用的参数（experiments.md 12.1：shield_def_bonus=基线·λ，shield_dr=基线·λ）；a 缩放支援增益
LAMBDA_PARAMS = ("combat.shield.defense_bonus", "combat.shield.damage_reduction")
A_PARAM = "combat.support.default_gain_scale"


class SearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    lambda_value: float
    support_gain_scale: float


@dataclass(frozen=True)
class Perturbation:
    perturbation_id: str
    parameter_name: Optional[str]
    factor: float
    label: str


@dataclass(frozen=True)
class RunSpec:
    candidate: Candidate
    perturbation: Perturbation
    split: str
    seed: int

    @property
    def replicate_id(self) -> str:
        return f"seed:{self.seed}"


@dataclass
class SearchSetup:
    base: ResolvedConfig
    entry: Path
    common_overrides: dict
    candidates: list[Candidate]
    perturbations: list[Perturbation]
    train_seeds: list[int]
    validation_seeds: list[int]
    experiment_id: str
    scale_label: str
    log_mode: str
    specs: list[RunSpec] = field(default_factory=list)


def _fmt(x: float) -> str:
    return f"{x:g}"


def candidates(cfg: ResolvedConfig) -> list[Candidate]:
    g = cfg.module("experiments")["grid"]
    return [Candidate(f"L{_fmt(l)}_A{_fmt(a)}", float(l), float(a))
            for l in g["mechanism_strength"] for a in g["support_gain_scale"]]


def perturbations(cfg: ResolvedConfig) -> list[Perturbation]:
    ps = cfg.module("experiments")["pain_sensitivity"]
    if ps["method"] != "one_factor_at_a_time":
        raise SearchError(f"不支持的扰动方法 {ps['method']}")
    out = [Perturbation(P0, None, 1.0, "基线")]
    for name in ps["parameters"]:
        for f in ps["factors"]:
            if f == 1:
                continue
            out.append(Perturbation(f"{name.split('.')[-1]}_x{_fmt(f)}", name, float(f), f"{name}×{_fmt(f)}"))
    return out


def baseline_candidate(cfg: ResolvedConfig, cands: Sequence[Candidate]) -> Candidate:
    b = cfg.module("experiments")["baseline_grid_point"]
    for c in cands:
        if c.lambda_value == b["mechanism_strength"] and c.support_gain_scale == b["support_gain_scale"]:
            return c
    raise SearchError("基准网格点不在候选集合中")


def run_overrides(base: ResolvedConfig, cand: Candidate, pert: Perturbation, common: Mapping[str, Any]) -> dict:
    ov = dict(common)
    if base.module("combat")["model_version"].startswith("proxy_v2"):
        ov["combat.research_v2.mechanism_strength"] = cand.lambda_value
    else:
        for p in LAMBDA_PARAMS:
            ov[p] = base.get(p) * cand.lambda_value
    ov[A_PARAM] = base.get(A_PARAM) * cand.support_gain_scale
    if pert.parameter_name:
        ov[pert.parameter_name] = base.get(pert.parameter_name) * pert.factor
    ov["experiments.base_scenario"] = base.module("experiments")["base_scenario"]
    return ov


def plan_search(entry: Path | str, *, n_players: Optional[int] = None, train_seeds: Optional[Sequence[int]] = None,
                validation_seeds: Optional[Sequence[int]] = None, candidate_ids: Optional[Sequence[str]] = None,
                perturbation_ids: Optional[Sequence[str]] = None, log_mode: Optional[str] = None) -> SearchSetup:
    entry = Path(entry)
    base = resolve_config(entry)
    exp = base.module("experiments")
    meta = base.metadata["module_metadata"]["experiments"]
    common: dict = {}
    full_n = base.module("population")["n_players"]
    if n_players is not None and n_players != full_n:
        common["population.n_players"] = int(n_players)
    cands = candidates(base)
    perts = perturbations(base)
    if candidate_ids:
        cands = [c for c in cands if c.candidate_id in set(candidate_ids)]
    if perturbation_ids:
        perts = [p for p in perts if p.perturbation_id in set(perturbation_ids)]
    tr = list(train_seeds if train_seeds is not None else meta["train_seeds"])
    va = list(validation_seeds if validation_seeds is not None else meta["validation_seeds"])
    for split, seeds in ((SPLIT_TRAIN, tr), (SPLIT_VALID, va)):
        if not seeds or any(isinstance(s, bool) or not isinstance(s, int) or s < 0 for s in seeds):
            raise SearchError(f"{split} seeds 必须是非空的非负整数列表")
        if len(set(seeds)) != len(seeds):
            raise SearchError(f"{split} seeds 不能重复")
    if set(tr).intersection(va):
        raise SearchError("train 与 validation seeds 必须互斥，不能复用同一次运行作独立复核")
    full = (not common and len(cands) == len(candidates(base)) and len(perts) == len(perturbations(base))
            and tr == list(meta["train_seeds"]) and va == list(meta["validation_seeds"]))
    scale = "full_protocol" if full else "reduced_scale"
    eid = "search_" + sha256_hex([SEARCH_PROTOCOL, exp["protocol_version"], base.config_id, common,
                                  [c.candidate_id for c in cands], [p.perturbation_id for p in perts], tr, va])[:12]
    setup = SearchSetup(base, entry, common, cands, perts, tr, va, eid, scale,
                        log_mode or meta.get("grid_log_mode", "summary"))
    if setup.log_mode not in ("full", "summary"):
        raise SearchError(f"未知日志模式 {setup.log_mode!r}")
    for c in cands:
        for s in tr:
            setup.specs.append(RunSpec(c, perts[0] if perts and perts[0].perturbation_id == P0 else
                                       Perturbation(P0, None, 1.0, "基线"), SPLIT_TRAIN, s))
        for p in perts:
            for s in va:
                setup.specs.append(RunSpec(c, p, SPLIT_VALID, s))
    return setup


# --------------------------------------------------------------------------- 执行（子进程）


def _execute_one(args: tuple) -> dict:
    entry, overrides, seed, runs_dir, log_mode, core_tier = args
    cfg = resolve_config(entry, overrides=overrides)
    t0 = time.perf_counter()
    tmp = Path(runs_dir) / f".tmp_{os.getpid()}_{seed}_{cfg.config_id[:12]}.sqlite"
    if tmp.exists():
        tmp.unlink()
    w = SQLiteWriter(tmp, log_mode=log_mode)
    try:
        res = run_simulation(cfg, master_seed=seed, writer=w)
        obj = run_objectives(w, res.run_id, core_tier)
        _require_rankable(obj.core_retention_end, obj.core_initial, seed=seed)
    finally:
        w.close()
    final = Path(runs_dir) / f"{res.run_id}.sqlite"
    os.replace(tmp, final)  # 完成后原子发布
    return {"run_id": res.run_id, "config_id": cfg.config_id, "seed": seed, "db": str(final),
            "total_revenue_fen": obj.total_revenue_fen, "core_retention_end": obj.core_retention_end,
            "core_initial": obj.core_initial, "seconds": time.perf_counter() - t0}


def execute(setup: SearchSetup, out_dir: Path | str, *, workers: int = 1, progress=None) -> dict[tuple, dict]:
    """运行去重后的全部逻辑 run；已存在且完成的 run 数据库直接复用。返回 (config_id, seed) → 结果。"""
    out = Path(out_dir)
    runs_dir = out / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    core = setup.base.module("population")["core_retention_tier"]
    jobs: dict[tuple, tuple] = {}
    for spec in setup.specs:
        ov = run_overrides(setup.base, spec.candidate, spec.perturbation, setup.common_overrides)
        cid = resolve_config(setup.entry, overrides=ov).config_id
        jobs.setdefault((cid, spec.seed), (str(setup.entry), ov, spec.seed, str(runs_dir), setup.log_mode, core))
    results: dict[tuple, dict] = {}
    index = _index_completed(runs_dir, required_log_mode=setup.log_mode)
    todo = []
    for key, args in jobs.items():
        if key in index:
            results[key] = _load_existing(index[key], key, core, required_log_mode=setup.log_mode)
        else:
            todo.append((key, args))
    if workers <= 1:
        for i, (key, args) in enumerate(todo):
            results[key] = _execute_one(args)
            if progress:
                progress(i + 1, len(todo))
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_execute_one, a): k for k, a in todo}
            for i, f in enumerate(futs):
                results[futs[f]] = f.result()
                if progress:
                    progress(i + 1, len(todo))
    return results


def _read_only(db: Path) -> sqlite3.Connection:
    if not db.is_file():
        raise SearchError(f"缺少运行数据库: {db}")
    return sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)


class _RunReader:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def query(self, sql: str, params=()) -> list[tuple]:
        return self.conn.execute(sql, params).fetchall()


def _validate_completed(conn: sqlite3.Connection, run_id: str, *,
                        expected_key: Optional[tuple] = None,
                        required_log_mode: str = "summary") -> tuple[str, int]:
    """只读校验复用条件，不通过建表或更新索引来修补缓存。"""
    row = conn.execute(
        "SELECT config_id,master_seed,status,config_json,experiment_id,replicate_id "
        "FROM run_manifest WHERE run_id=?", (run_id,)).fetchone()
    if row is None or row[2] != "completed":
        raise SearchError(f"run {run_id} 未完成")
    cid, seed, _, config_json, experiment_id, replicate_id = row
    key = (cid, seed)
    if expected_key is not None and key != expected_key:
        raise SearchError(f"run {run_id} 的config/seed与请求不一致")
    science = json.loads(config_json)
    if compute_config_id(science) != cid:
        raise SearchError(f"run {run_id} 的配置摘要不匹配")
    identity = RunIdentity(experiment_id, cid, replicate_id, seed)
    if identity.run_id != run_id:
        raise SearchError(f"run {run_id} 的运行身份不匹配")
    attempt = conn.execute(
        "SELECT status,log_mode FROM run_attempt WHERE run_id=? ORDER BY attempt_id DESC LIMIT 1",
        (run_id,)).fetchone()
    if attempt is None or attempt[0] != "completed" or attempt[1] not in ("full", "summary"):
        raise SearchError(f"run {run_id} 没有已完成的有效attempt")
    if required_log_mode == "full" and attempt[1] != "full":
        raise SearchError(f"run {run_id} 仅有summary日志，不能作为full复用")
    if required_log_mode not in ("full", "summary"):
        raise SearchError(f"未知日志模式 {required_log_mode!r}")
    if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise SearchError(f"run {run_id} 数据库完整性检查失败")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise SearchError(f"run {run_id} 外键检查失败")
    n = science["modules"]["population"]["n_players"]
    periods = science["modules"]["scenarios"]["timeline"]["n_periods"]
    if any(isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in (n, periods)):
        raise SearchError(f"run {run_id} 的人群/时间规模非法")
    expected = [(t, n, n, 0, n - 1) for t in range(1, periods + 1)]
    for table in ("fact_player_lifecycle", "agg_player_period"):
        rows = conn.execute(
            f"SELECT t_idx,COUNT(*),COUNT(DISTINCT player_id),MIN(player_id),MAX(player_id) "
            f"FROM {table} WHERE run_id=? GROUP BY t_idx ORDER BY t_idx", (run_id,)).fetchall()
        bad_type = conn.execute(
            f"SELECT 1 FROM {table} WHERE run_id=? AND "
            "(typeof(player_id)<>'integer' OR typeof(t_idx)<>'integer') LIMIT 1", (run_id,)).fetchone()
        if rows != expected or bad_type is not None:
            raise SearchError(f"run {run_id} 的{table}不完整，应有{n}×{periods}条玩家期记录")
    mismatch = conn.execute(
        "SELECT 1 FROM agg_player_period a JOIN fact_player_lifecycle l "
        "USING(run_id,player_id,t_idx) WHERE a.run_id=? AND "
        "(a.player_tier IS NOT l.player_tier OR a.active_start IS NOT l.active_start "
        "OR a.active_end IS NOT l.active_end OR a.CI_pre IS NOT l.CI_pre OR a.CI_post IS NOT l.CI_post) LIMIT 1",
        (run_id,)).fetchone()
    if mismatch:
        raise SearchError(f"run {run_id} 的生命周期与玩家期汇总不一致")
    if required_log_mode == "full":
        pulls = conn.execute("SELECT COUNT(*) FROM fact_pull_event WHERE run_id=?", (run_id,)).fetchone()[0]
        expected_pulls = conn.execute(
            "SELECT SUM(total_pulls) FROM agg_player_period WHERE run_id=?", (run_id,)).fetchone()[0]
        post_periods = conn.execute(
            "SELECT COUNT(*) FROM (SELECT player_id,t_idx FROM fact_player_roster_panel "
            "WHERE run_id=? AND eval_phase='post' GROUP BY player_id,t_idx)", (run_id,)).fetchone()[0]
        if pulls != expected_pulls or post_periods != n * periods:
            raise SearchError(f"run {run_id} 的full抽取/角色日志不完整")
    return key


def _require_rankable(retention: Optional[float], core_initial: int, *, seed: int) -> None:
    if (core_initial <= 0 or retention is None or isinstance(retention, bool)
            or not isinstance(retention, (int, float)) or not math.isfinite(retention)
            or not 0 <= retention <= 1):
        raise SearchError(f"seed {seed} 的核心队列留存不可评估；增加样本或调整预注册队列后另建实验，不能计算前沿")


def _index_completed(runs_dir: Path, *, required_log_mode: str = "summary") -> dict[tuple, tuple[str, Path]]:
    """仅索引完成、完整且日志能力满足请求的运行；损坏缓存不当成已完成结果。"""
    idx = {}
    for db in sorted(runs_dir.glob("*.sqlite")):
        if db.name.startswith(".tmp"):
            continue
        con = None
        try:
            con = _read_only(db)
            for (run_id,) in con.execute("SELECT run_id FROM run_manifest WHERE status='completed'").fetchall():
                key = _validate_completed(con, run_id, required_log_mode=required_log_mode)
                idx[key] = (run_id, db)
        except (sqlite3.Error, ValueError, KeyError, TypeError, SearchError):
            # 只忽略无效候选文件；execute会重新执行缺失的逻辑run，原文件在成功发布前不改变。
            continue
        finally:
            if con is not None:
                con.close()
    return idx


def _load_existing(hit: tuple[str, Path], key: tuple, core: str, *, required_log_mode: str = "summary") -> dict:
    run_id, db = hit
    conn = _read_only(db)
    try:
        _validate_completed(conn, run_id, expected_key=key, required_log_mode=required_log_mode)
        obj = run_objectives(_RunReader(conn), run_id, core)
        _require_rankable(obj.core_retention_end, obj.core_initial, seed=key[1])
    finally:
        conn.close()
    return {"run_id": run_id, "config_id": key[0], "seed": key[1], "db": str(db), "reused": True,
            "total_revenue_fen": obj.total_revenue_fen, "core_retention_end": obj.core_retention_end,
            "core_initial": obj.core_initial, "seconds": 0.0}


# --------------------------------------------------------------------------- 统计


def dominates(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] >= b[0] and a[1] >= b[1] and (a[0] > b[0] or a[1] > b[1])


def nondominated(points: Mapping[str, tuple[float, float]]) -> set[str]:
    """两目标均不差且至少一项更好即支配；数值完全相同者全部保留。"""
    return {k for k, p in points.items() if not any(dominates(q, p) for j, q in points.items() if j != k)}


def average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """并列取平均秩的 Pearson 相关；任一侧无变异时 NULL。"""
    if len(x) != len(y) or len(x) < 2:
        return None
    rx, ry = average_ranks(x), average_ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy)


def _sd(v: Sequence[float]) -> Optional[float]:
    return statistics.stdev(v) if len(v) >= 2 else None


def assemble(setup: SearchSetup, results: Mapping[tuple, dict], search_db: Path | str) -> dict:
    """整份汇总成功才发布；任何导入/分析错误都保留上一份search.sqlite。"""
    search_db = Path(search_db)
    for result in results.values():
        _require_rankable(result["core_retention_end"], result["core_initial"], seed=result["seed"])
    search_db.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{search_db.stem}_", suffix=".tmp.sqlite", dir=search_db.parent)
    os.close(fd)
    staged = Path(name)
    try:
        if search_db.exists():
            source = _read_only(search_db)
            target = sqlite3.connect(staged)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        summary = _assemble_into(setup, results, staged)
        os.replace(staged, search_db)
        return summary
    finally:
        staged.unlink(missing_ok=True)


def _assemble_into(setup: SearchSetup, results: Mapping[tuple, dict], search_db: Path) -> dict:
    w = SQLiteWriter(search_db)
    try:
        return _assemble_rows(setup, results, w)
    finally:
        if w.conn.in_transaction:
            w.conn.rollback()
        w.close()


def _assemble_rows(setup: SearchSetup, results: Mapping[tuple, dict], w: SQLiteWriter) -> dict:
    w.conn.executescript(ANALYTICS_SCHEMA.read_text(encoding="utf-8"))
    base_cand = baseline_candidate(setup.base, setup.candidates) if any(
        c.candidate_id == baseline_candidate(setup.base, candidates(setup.base)).candidate_id
        for c in setup.candidates) else None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for tb in ("agg_search_results", "agg_seed_robustness", "agg_rank_stability", "agg_parameter_sensitivity"):
        w.conn.execute(f"DELETE FROM {tb} WHERE experiment_id=?", (setup.experiment_id,))  # 汇总可重算
    w.conn.execute("INSERT OR REPLACE INTO experiment_manifest VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (setup.experiment_id, SEARCH_PROTOCOL, setup.base.config_id, setup.scale_label,
                    json.dumps(setup.common_overrides, sort_keys=True), json.dumps(setup.train_seeds),
                    json.dumps(setup.validation_seeds), setup.log_mode, now, "running"))
    # 合并 run 清单与玩家期宽表（只合并 completed）
    merged = set(r[0] for r in w.conn.execute("SELECT run_id FROM run_manifest"))
    for res in sorted(results.values(), key=lambda r: r["run_id"]):
        key = (res["config_id"], res["seed"])
        src_conn = _read_only(Path(res["db"]))
        try:
            _validate_completed(src_conn, res["run_id"], expected_key=key, required_log_mode=setup.log_mode)
            obj = run_objectives(_RunReader(src_conn), res["run_id"],
                                 setup.base.module("population")["core_retention_tier"])
            if (obj.total_revenue_fen, obj.core_initial, obj.core_retention_end) != (
                    res["total_revenue_fen"], res["core_initial"], res["core_retention_end"]):
                raise SearchError(f"run {res['run_id']} 的目标摘要与数据库不一致")
        finally:
            src_conn.close()
        if res["run_id"] in merged:
            _validate_completed(w.conn, res["run_id"], expected_key=key)
            continue
        w.conn.execute("ATTACH DATABASE ? AS src", (res["db"],))
        try:
            st = w.conn.execute("SELECT status FROM src.run_manifest WHERE run_id=?", (res["run_id"],)).fetchone()
            if not st or st[0] != "completed":
                raise SearchError(f"run {res['run_id']} 未完成，不能合并")
            w.conn.execute("BEGIN")
            for tb in ("run_manifest", "run_attempt", "stage_timing", "agg_player_period", "fact_player_lifecycle"):
                w.conn.execute(f"INSERT INTO main.{tb} SELECT * FROM src.{tb} WHERE run_id=?", (res["run_id"],))
            w.conn.execute("COMMIT")
        except BaseException:
            if w.conn.in_transaction:
                w.conn.rollback()
            raise
        finally:
            w.conn.execute("DETACH DATABASE src")
        merged.add(res["run_id"])
    w.write_dim("dim_search_candidate", [{"experiment_id": setup.experiment_id, "candidate_id": c.candidate_id,
                                          "lambda_value": c.lambda_value, "support_gain_scale": c.support_gain_scale}
                                         for c in setup.candidates])
    w.write_dim("dim_perturbation", [{"experiment_id": setup.experiment_id, "perturbation_id": p.perturbation_id,
                                      "override_json": json.dumps({p.parameter_name: p.factor} if p.parameter_name
                                                                  else {}, sort_keys=True), "label": p.label}
                                     for p in setup.perturbations])
    rows: dict[tuple, dict] = {}
    for spec in setup.specs:
        ov = run_overrides(setup.base, spec.candidate, spec.perturbation, setup.common_overrides)
        cid = resolve_config(setup.entry, overrides=ov).config_id
        r = results[(cid, spec.seed)]
        key = (spec.candidate.candidate_id, spec.perturbation.perturbation_id, spec.split, spec.seed)
        rows[key] = {"spec": spec, **r}
        w.write_dim("search_run_map", [{"experiment_id": setup.experiment_id, "candidate_id": key[0],
                                        "perturbation_id": key[1], "split": key[2], "replicate_id": spec.replicate_id,
                                        "config_id": cid, "run_id": r["run_id"]}])
    # 每 (split, 扰动, seed) 独立求非支配集合与相对基准差
    groups: dict[tuple, dict[str, dict]] = {}
    for (c, p, s, seed), r in rows.items():
        groups.setdefault((s, p, seed), {})[c] = r
    for (s, p, seed), g in groups.items():
        for r in g.values():
            _require_rankable(r["core_retention_end"], r["core_initial"], seed=seed)
        pts = {c: (float(r["total_revenue_fen"]), r["core_retention_end"]) for c, r in g.items()}
        nd = nondominated(pts)
        b = g.get(base_cand.candidate_id) if base_cand else None
        for c, r in g.items():
            r["nondominated"] = c in nd
            r["delta_rev"] = None if b is None else r["total_revenue_fen"] - b["total_revenue_fen"]
            r["delta_ret"] = None if (b is None or r["core_retention_end"] is None or b["core_retention_end"] is None) \
                else r["core_retention_end"] - b["core_retention_end"]
    with w.atomic_buffer():
        for (c, p, s, seed), r in sorted(rows.items()):
            w.insert("agg_search_results", {
                "experiment_id": setup.experiment_id, "candidate_id": c, "perturbation_id": p, "split": s,
                "replicate_id": f"seed:{seed}", "master_seed": seed, "config_id": r["config_id"],
                "run_id": r["run_id"], "total_revenue_fen": r["total_revenue_fen"],
                "core_retention_end": r["core_retention_end"], "core_initial": r["core_initial"],
                "delta_revenue_vs_base": r["delta_rev"], "delta_retention_vs_base": r["delta_ret"],
                "nondominated": int(r["nondominated"])})
    # 稳健性：(候选, 扰动, split) 的种子分布
    robust: dict[tuple, dict] = {}
    by_cps: dict[tuple, list[dict]] = {}
    for (c, p, s, seed), r in rows.items():
        by_cps.setdefault((c, p, s), []).append(r)
    with w.atomic_buffer():
        for (c, p, s), rs in sorted(by_cps.items()):
            rev = [float(r["total_revenue_fen"]) for r in rs]
            ret = [r["core_retention_end"] for r in rs if r["core_retention_end"] is not None]
            drv = [r["delta_rev"] for r in rs if r["delta_rev"] is not None]
            drt = [r["delta_ret"] for r in rs if r["delta_ret"] is not None]
            inc = sum(r["nondominated"] for r in rs)
            row = {"experiment_id": setup.experiment_id, "candidate_id": c, "perturbation_id": p, "split": s,
                   "n_seeds": len(rs), "revenue_mean": sum(rev) / len(rev), "revenue_sd": _sd(rev),
                   "revenue_min": min(rev), "revenue_max": max(rev),
                   "retention_mean": sum(ret) / len(ret) if ret else None, "retention_sd": _sd(ret),
                   "retention_min": min(ret) if ret else None, "retention_max": max(ret) if ret else None,
                   "delta_revenue_mean": sum(drv) / len(drv) if drv else None,
                   "delta_retention_mean": sum(drt) / len(drt) if drt else None,
                   "inclusion_count": inc, "inclusion_frequency": inc / len(rs)}
            robust[(c, p, s)] = row
            w.insert("agg_seed_robustness", row)
    # 秩稳定：P0 下筛选均值 vs 复核均值；各扰动复核均值 vs P0 复核均值
    cids = [c.candidate_id for c in setup.candidates]
    stab = []

    def means(p, s, key):
        return [robust.get((c, p, s), {}).get(key) for c in cids]

    def add_rho(pid, comp, a_rev, b_rev, a_ret, b_ret):
        ok_rev = None not in a_rev and None not in b_rev
        ok_ret = None not in a_ret and None not in b_ret
        r = {"experiment_id": setup.experiment_id, "perturbation_id": pid, "comparison": comp,
             "n_candidates": len(cids), "rho_rev": spearman(a_rev, b_rev) if ok_rev else None,
             "rho_ret": spearman(a_ret, b_ret) if ok_ret else None}
        stab.append(r)
        w.insert("agg_rank_stability", r)

    with w.atomic_buffer():
        if setup.train_seeds and setup.validation_seeds:
            add_rho(P0, "train_mean_vs_validation_mean", means(P0, SPLIT_TRAIN, "revenue_mean"),
                    means(P0, SPLIT_VALID, "revenue_mean"), means(P0, SPLIT_TRAIN, "retention_mean"),
                    means(P0, SPLIT_VALID, "retention_mean"))
        for p in setup.perturbations:
            if p.perturbation_id == P0:
                continue
            add_rho(p.perturbation_id, "validation_vs_P0_validation", means(P0, SPLIT_VALID, "revenue_mean"),
                    means(p.perturbation_id, SPLIT_VALID, "revenue_mean"), means(P0, SPLIT_VALID, "retention_mean"),
                    means(p.perturbation_id, SPLIT_VALID, "retention_mean"))
            for c in cids:
                a, b = robust.get((c, P0, SPLIT_VALID)), robust.get((c, p.perturbation_id, SPLIT_VALID))
                if not a or not b:
                    continue
                rel = lambda x, y: None if (x in (None, 0) or y is None) else (y - x) / x  # noqa: E731
                w.insert("agg_parameter_sensitivity", {
                    "experiment_id": setup.experiment_id, "perturbation_id": p.perturbation_id,
                    "parameter_name": p.parameter_name, "factor": p.factor, "candidate_id": c,
                    "revenue_mean": b["revenue_mean"], "retention_mean": b["retention_mean"],
                    "rel_change_revenue_vs_P0": rel(a["revenue_mean"], b["revenue_mean"]),
                    "rel_change_retention_vs_P0": rel(a["retention_mean"], b["retention_mean"]),
                    "nondominated_freq": b["inclusion_frequency"]})
    w.flush()
    n_unique = len(results)
    secs = sum(r.get("seconds", 0.0) for r in results.values())
    if w.conn.execute("PRAGMA foreign_key_check").fetchall():
        raise SearchError("搜索汇总外键检查失败，不发布")
    w.conn.execute("UPDATE experiment_manifest SET status='completed' WHERE experiment_id=?", (setup.experiment_id,))
    return {"experiment_id": setup.experiment_id, "scale": setup.scale_label, "logical_runs": len(rows),
            "unique_runs": n_unique, "run_seconds_total": secs, "rank_stability": stab}


def write_manifest(setup: SearchSetup, out_dir: Path | str, summary: Mapping[str, Any]) -> Path:
    p = Path(out_dir) / "experiment_manifest.json"
    p.write_text(json.dumps({
        "experiment_id": setup.experiment_id, "protocol": SEARCH_PROTOCOL, "scale": setup.scale_label,
        "base_config_id": setup.base.config_id, "common_overrides": setup.common_overrides,
        "candidates": [c.candidate_id for c in setup.candidates],
        "perturbations": [p.perturbation_id for p in setup.perturbations],
        "train_seeds": setup.train_seeds, "validation_seeds": setup.validation_seeds, "log_mode": setup.log_mode,
        "summary": {k: v for k, v in summary.items() if k != "rank_stability"},
        "rank_stability": summary.get("rank_stability"),
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return p


def scenario_overrides(base: ResolvedConfig, scenario_name: str, common: Mapping[str, Any]) -> dict:
    if scenario_name not in base.module("scenarios")["reference_scenarios"]:
        raise SearchError(f"未知参照情景 {scenario_name}")
    return {**common, "experiments.base_scenario": scenario_name}


def reference_scenario_names(base: ResolvedConfig) -> list[str]:
    return sorted(deep_thaw(base.module("scenarios")["reference_scenarios"]))
