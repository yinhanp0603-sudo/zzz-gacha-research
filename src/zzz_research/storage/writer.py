"""M-STORAGE：SQLite 写入端口。

只把 EventBundle 与 runtime 已算好的记录投影为物理行；不补算价格、保底、资产或效用。
批量缓冲、按块事务提交；外键延迟到提交时检查。summary 模式不落每抽与每角色明细。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence

from ..contracts import EventBundle, canonical_json

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
# 写入器实现版本；schema.sql 的物理格式版本仍为 storage_v1。
STORAGE_VERSION = "storage_writer_v3_spending"

LOG_FULL = "full"
LOG_SUMMARY = "summary"

#: summary 模式不持久化的明细表
DETAIL_TABLES = frozenset({
    "fact_pull_event", "fact_asset_event", "fact_resource_event", "fact_cash_event",
    "fact_player_roster_panel", "fact_decision_log", "fact_ownership", "dim_team_snapshot",
})


#: write_bundle 投影到的全部表（summary 模式据此判断是否整体跳过）
_BUNDLE_TABLES = frozenset({"fact_cash_event", "fact_pull_event", "fact_asset_event", "fact_resource_event"})


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class PullAnnotation:
    """runtime 提供的抽取上下文（writer 不重算 p_S 或目标阶段）。"""

    p_s: float
    target_stage: Optional[str]


@dataclass(frozen=True)
class BundleAnnotation:
    period_id: str
    pulls: Mapping[str, PullAnnotation]
    #: resource_event_id → (earned_period, cash_event_id 或 None)
    resources: Mapping[str, tuple[int, Optional[str]]]


def _b(x: Optional[bool]) -> Optional[int]:
    return None if x is None else int(bool(x))


class SQLiteWriter:
    """PersistencePort 实现。用法：open → write_* → flush（可多次）→ finish。"""

    def __init__(self, path: str | Path, *, log_mode: str = LOG_FULL, chunk_rows: int = 20000):
        if log_mode not in (LOG_FULL, LOG_SUMMARY):
            raise StorageError(f"未知日志模式 {log_mode!r}")
        self.path = str(path)
        self.log_mode = log_mode
        self.chunk_rows = chunk_rows
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self._buf: dict[str, tuple[tuple[str, ...], list[tuple]]] = {}
        self._pending = 0
        self._atomic_depth = 0
        self.run_id: Optional[str] = None
        self.attempt_id: Optional[int] = None
        self.row_counts: dict[str, int] = {}

    # ------------------------------------------------------------------ 基础
    def insert(self, table: str, row: Mapping[str, Any]) -> None:
        if self.log_mode == LOG_SUMMARY and table in DETAIL_TABLES:
            return
        cols = tuple(row)
        slot = self._buf.get(table)
        if slot is None:
            self._buf[table] = slot = (cols, [])
        elif slot[0] != cols:
            raise StorageError(f"{table} 行字段不一致: {cols} vs {slot[0]}")
        slot[1].append(tuple(row[c] for c in cols))
        self._pending += 1
        if self._atomic_depth == 0 and self._pending >= self.chunk_rows:
            self.flush()

    @contextmanager
    def atomic_buffer(self) -> Iterator["SQLiteWriter"]:
        """一个可嵌套的投影边界；失败撤回本边界追加的行，外层退出后才自动提交。

        只记录各表原长度，不复制已有行。调用方可将事件包及其派生行放在同一边界内。
        """
        lengths = {table: len(slot[1]) for table, slot in self._buf.items()}
        pending = self._pending
        self._atomic_depth += 1
        try:
            yield self
        except BaseException:
            for table in list(self._buf):
                if table not in lengths:
                    del self._buf[table]
                else:
                    del self._buf[table][1][lengths[table]:]
            self._pending = pending
            raise
        finally:
            self._atomic_depth -= 1
        # 提交失败时由 flush 回滚数据库并清空未提交缓冲，留给整次运行标记失败。
        if self._atomic_depth == 0 and self._pending >= self.chunk_rows:
            self.flush()

    def flush(self) -> None:
        """一个事务写入全部缓冲；外键在 COMMIT 时检查，失败整体回滚。"""
        if self._atomic_depth:
            raise StorageError("atomic_buffer 尚未结束，不能提交不完整事件包")
        if not self._buf:
            return
        cur = self.conn.cursor()
        counts = {}
        try:
            cur.execute("BEGIN")
            for table, (cols, rows) in self._buf.items():
                ph = ",".join("?" * len(cols))
                cur.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})", rows)
                counts[table] = len(rows)
            cur.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        else:
            for table, count in counts.items():
                self.row_counts[table] = self.row_counts.get(table, 0) + count
        finally:
            self._buf.clear()
            self._pending = 0
            cur.close()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ 运行
    def start_run(self, *, run_id: str, experiment_id: str, config_id: str, replicate_id: str,
                  master_seed: int, rng_version: str, algorithm_versions: Mapping[str, str],
                  config_science: Mapping[str, Any], input_manifest_hash: str,
                  environment: Mapping[str, Any], attempt_id: int = 0,
                  code_revision: Optional[str] = None) -> None:
        exists = self.conn.execute("SELECT 1 FROM run_manifest WHERE run_id=?", (run_id,)).fetchone()
        if exists is not None:
            raise StorageError("M3 同一数据库不覆盖已有 run_id（包括其他 attempt）；重跑请使用独立数据库")
        with closing(self.conn.cursor()) as cur:
            try:
                cur.execute("BEGIN IMMEDIATE")
                if cur.execute("SELECT 1 FROM run_manifest WHERE run_id=?", (run_id,)).fetchone():
                    raise StorageError("M3 同一数据库不覆盖已有 run_id；重跑请使用独立数据库")
                cur.execute(
                    "INSERT INTO run_manifest VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, experiment_id, config_id, replicate_id, master_seed, rng_version,
                     canonical_json(dict(algorithm_versions)), canonical_json(config_science),
                     input_manifest_hash, code_revision, canonical_json(dict(environment)), "running"))
                cur.execute("INSERT INTO run_attempt VALUES (?,?,?,?,?,?,?,?)",
                            (run_id, attempt_id, self.log_mode,
                             datetime.now(timezone.utc).isoformat(timespec="seconds"), None, "running", None, None))
                cur.execute("COMMIT")
            except BaseException:
                if self.conn.in_transaction:
                    self.conn.rollback()
                raise
        self.run_id, self.attempt_id = run_id, attempt_id

    def finish_run(self, *, status: str, error_code: Optional[str] = None,
                   output_hash: Optional[str] = None,
                   stage_timings: Sequence[tuple[str, float, Optional[int]]] = ()) -> None:
        if self.run_id is None:
            raise StorageError("尚未 start_run")
        if status == "completed":
            self.flush()
        else:
            self._buf.clear()
            self._pending = 0
        with closing(self.conn.cursor()) as cur:
            try:
                cur.execute("BEGIN")
                for stage, secs, rows in stage_timings:
                    cur.execute("INSERT INTO stage_timing VALUES (?,?,?,?,?,?,?)",
                                (self.run_id, self.attempt_id, stage, secs, rows, None, "time.perf_counter"))
                cur.execute("UPDATE run_attempt SET ended_at=?, status=?, error_code=?, output_hash=? "
                            "WHERE run_id=? AND attempt_id=?",
                            (datetime.now(timezone.utc).isoformat(timespec="seconds"), status, error_code,
                             output_hash, self.run_id, self.attempt_id))
                cur.execute("UPDATE run_manifest SET status=? WHERE run_id=?", (status, self.run_id))
                cur.execute("COMMIT")
            except BaseException:
                if self.conn.in_transaction:
                    self.conn.rollback()
                raise

    # ------------------------------------------------------------------ 事件包
    def write_bundle(self, bundle: EventBundle, annotation: Optional[BundleAnnotation] = None) -> None:
        """单抽或时期事件包 → 抽取/资产/资源/现金行。annotation 由 runtime 提供。"""
        if annotation is None:
            raise StorageError("write_bundle 需要 runtime 提供的 BundleAnnotation")
        if self.log_mode == LOG_SUMMARY and _BUNDLE_TABLES <= DETAIL_TABLES:
            return  # summary 模式下事件包投影的四张表全部不落盘，跳过逐行投影
        with self.atomic_buffer():
            self._project_bundle(bundle, annotation)

    def _project_bundle(self, bundle: EventBundle, annotation: BundleAnnotation) -> None:
        run, pid, t = bundle.run_id, bundle.player_id, bundle.t_idx
        cash_by_pull = {e.related_pull_id: e.event_id for e in bundle.cash_events if e.related_pull_id}
        for e in bundle.cash_events:
            self.insert("fact_cash_event", {
                "run_id": run, "cash_event_id": e.event_id, "player_id": pid, "t_idx": t,
                "event_day": e.day, "purchase_kind": e.product, "amount_fen": e.amount_fen,
                "granted_credit_minor": e.credit_minor_granted, "pull_event_id": e.related_pull_id})
        for p in bundle.pulls:
            ann = annotation.pulls[p.pull_id]
            self.insert("fact_pull_event", {
                "run_id": run, "player_id": pid, "t_idx": t, "banner_id": p.banner_id,
                "pool_kind": p.pool_kind.value, "draw_slot": p.draw_slot, "pull_event_id": p.pull_id,
                "pity_group": p.pity_before.pity_group, "target_stage": ann.target_stage, "p_S": ann.p_s,
                "is_S": int(p.is_s), "is_UP": int(p.is_up), "result_asset_id": p.result_asset_id,
                "pity_before": p.pity_before.pulls_since_s, "pity_after": p.pity_after.pulls_since_s,
                "guaranteed_before": int(p.pity_before.guaranteed_up),
                "guaranteed_after": int(p.pity_after.guaranteed_up),
                "paid_cash_event_id": cash_by_pull.get(p.pull_id)})
        asset_pull = {a.event_id: p for p in bundle.pulls for a in p.asset_deltas}
        for a in bundle.asset_deltas:
            src = asset_pull.get(a.event_id)
            self.insert("fact_asset_event", {
                "run_id": run, "asset_event_id": a.event_id, "player_id": pid,
                "period_id": annotation.period_id, "event_day": a.day,
                "banner_id": None if src is None else src.banner_id,
                "pull_event_id": None if src is None else src.pull_id,
                "asset_kind": a.asset_kind.value, "asset_id": a.asset_id,
                "event_kind": "initial" if a.is_initial else "pull", "copies_delta": a.quantity})
        for d in bundle.resource_deltas:
            earned, cash_id = annotation.resources[d.event_id]
            kind = "grant" if d.delta_credit_minor > 0 else ("spend" if d.related_pull_id else "expire")
            self.insert("fact_resource_event", {
                "run_id": run, "resource_event_id": f"p{pid}/{d.event_id}", "player_id": pid, "t_idx": t,
                "event_day": d.day, "batch_id": d.batch_id, "provenance": d.source.value,
                "earned_period": earned, "event_kind": kind, "delta_minor": d.delta_credit_minor,
                "pull_event_id": d.related_pull_id, "cash_event_id": cash_id})

    # ------------------------------------------------------------------ 其他记录（runtime 已算好）
    def write_rows(self, table: str, rows: Iterable[Mapping[str, Any]]) -> None:
        for r in rows:
            self.insert(table, r)

    def write_dim(self, table: str, rows: Iterable[Mapping[str, Any]]) -> None:
        """维度表只复用相同主键且内容相同的行；新行仍执行全部数据库约束。"""
        info = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not info:
            raise StorageError(f"未知维度表 {table}")
        cols = tuple(c[1] for c in info)
        pk = tuple(c[1] for c in sorted(info, key=lambda c: c[5]) if c[5])
        if not pk:
            raise StorageError(f"维度表 {table} 没有主键")
        for r in rows:
            if set(r) - set(cols):
                raise StorageError(f"{table} 含未知字段: {sorted(set(r) - set(cols))}")
            values = []
            for _, name, _, required, default, is_pk in info:
                if name in r:
                    values.append(r[name])
                elif default is not None:
                    values.append(self.conn.execute(f"SELECT {default}").fetchone()[0])
                elif required or is_pk:
                    raise StorageError(f"{table} 缺少必需字段 {name}")
                else:
                    values.append(None)
            row = dict(zip(cols, values))
            existing = self.conn.execute(
                f"SELECT {','.join(cols)} FROM {table} WHERE " + " AND ".join(f"{c}=?" for c in pk),
                tuple(row[c] for c in pk)).fetchone()
            if existing is not None:
                if tuple(values) != existing:
                    raise StorageError(f"{table} 已存在相同主键但内容不同: {tuple(row[c] for c in pk)}")
                continue
            ph = ",".join("?" * len(cols))
            self.conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})", values)

    # ------------------------------------------------------------------ 读取（测试/对账）
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        self.flush()
        return self.conn.execute(sql, params).fetchall()

    def table_digest(self, table: str, exclude: Sequence[str] = ()) -> str:
        """按全部列排序后的内容摘要，用于复现性比较。"""
        import hashlib
        cols = [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})") if r[1] not in exclude]
        rows = self.conn.execute(f"SELECT {','.join(cols)} FROM {table} ORDER BY {','.join(cols)}").fetchall()
        return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()

