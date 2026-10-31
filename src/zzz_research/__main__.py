"""命令行入口（engineering.md 13.2）。

  python -m zzz_research validate-config --config config/experiment.json
  python -m zzz_research prepare-observations --out outputs/obs [--snapshot-dir DIR --snapshot-id ID --revision REV]
  python -m zzz_research build-benchmarks --config config/experiment.json --out outputs/bench
  python -m zzz_research simulate --config config/experiment.json --replicate 1101 --log-mode full [--players N] [--scenario NAME]
  python -m zzz_research aggregate --db <run.sqlite> [--run-id ID] --out outputs/tables
  python -m zzz_research search --config config/experiment.json --out outputs/search [--players N] [--workers W]
  python -m zzz_research report --dir outputs/<dir>
  python -m zzz_research app --dir outputs/<dir>
  python -m zzz_research run-all --config config/experiment.json --out outputs/<dir> [--players N] [--workers W]

simulate/report 不隐式联网；只有 prepare-observations 读取调用方显式提供的快照目录。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_CONFIG = "config/experiment.json"


def _ints(s: Optional[str]) -> Optional[list[int]]:
    return None if s is None else [int(x) for x in s.split(",") if x.strip()]


def _strs(s: Optional[str]) -> Optional[list[str]]:
    return None if s is None else [x.strip() for x in s.split(",") if x.strip()]


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _progress(i: int, n: int) -> None:
    print(f"\r  runs {i}/{n}", end="" if i < n else "\n", file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zzz_research", description="公开样本 + 战斗代理 + 假设行为情景")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("validate-config", help="解析配置、校验并输出 config_id")
    s.add_argument("--config", default=DEFAULT_CONFIG)

    s = sub.add_parser("prepare-observations", help="公开快照覆盖/导入（不隐式联网）")
    s.add_argument("--out", required=True)
    s.add_argument("--meta-dir", default=None, help="ShiyuStats 元数据快照目录")
    s.add_argument("--snapshot-dir", default=None, help="已下载的原始 CSV 目录（固定修订）")
    s.add_argument("--snapshot-id", default=None)
    s.add_argument("--revision", default=None, help="原始数据集的固定修订号")
    s.add_argument("--data-license", default=None, help="原始数据集明确声明的许可；不从代码仓库继承")

    s = sub.add_parser("build-benchmarks", help="L2 固定基准面板、配对、参考轨迹与 KM")
    s.add_argument("--config", default=DEFAULT_CONFIG)
    s.add_argument("--out", required=True)
    s.add_argument("--engine", default="proxy", choices=["proxy"])

    s = sub.add_parser("simulate", help="运行一个行为情景")
    s.add_argument("--config", default=DEFAULT_CONFIG)
    s.add_argument("--replicate", type=int, required=True, help="master_seed")
    s.add_argument("--log-mode", default="full", choices=["full", "summary"])
    s.add_argument("--players", type=int, default=None, help="覆盖 n_players（改变 config_id）")
    s.add_argument("--scenario", default=None, help="参照情景名（覆盖 experiments.base_scenario）")
    s.add_argument("--out", default="outputs/runs")

    s = sub.add_parser("aggregate", help="三口径、留存/现金与收入结构")
    s.add_argument("--db", required=True)
    s.add_argument("--run-id", default=None)
    s.add_argument("--out", default="outputs/tables")

    for name in ("search", "run-all"):
        s = sub.add_parser(name, help="25 点候选搜索" if name == "search" else "完整流水线")
        s.add_argument("--config", default=DEFAULT_CONFIG)
        s.add_argument("--out", required=True)
        s.add_argument("--players", type=int, default=None, help="缩减人群规模（报告中标注 reduced_scale）")
        s.add_argument("--workers", type=int, default=1)
        s.add_argument("--train-seeds", default=None)
        s.add_argument("--validation-seeds", default=None)
        s.add_argument("--candidates", default=None, help="逗号分隔的 candidate_id 子集")
        s.add_argument("--perturbations", default=None, help="逗号分隔的 perturbation_id 子集")
        if name == "run-all":
            s.add_argument("--reference-seed", type=int, default=None)
            s.add_argument("--meta-dir", default=None)
            s.add_argument("--snapshot-dir", default=None)
            s.add_argument("--snapshot-id", default=None)
            s.add_argument("--revision", default=None)

    s = sub.add_parser("report", help="从已完成产物生成 REPORT.md 与图")
    s.add_argument("--dir", required=True)
    s = sub.add_parser("app", help="生成静态看板（安装 streamlit 时可用 streamlit run）")
    s.add_argument("--dir", required=True)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    from . import pipeline as PL

    if args.cmd == "validate-config":
        _print(PL.validate_config(args.config))
    elif args.cmd == "prepare-observations":
        kw = {"meta_dir": args.meta_dir} if args.meta_dir else {}
        _print(PL.prepare_observations(args.out, snapshot_dir=args.snapshot_dir, snapshot_id=args.snapshot_id,
                                       revision=args.revision, data_license=args.data_license, **kw))
    elif args.cmd == "build-benchmarks":
        _print(PL.build_benchmarks(args.config, args.out))
    elif args.cmd == "simulate":
        _print(PL.simulate(args.config, args.out, seed=args.replicate, scenario=args.scenario,
                           n_players=args.players, log_mode=args.log_mode))
    elif args.cmd == "aggregate":
        _print(PL.aggregate_run(args.db, args.out, run_id=args.run_id))
    elif args.cmd == "search":
        _print(PL.search(args.config, args.out, n_players=args.players, workers=args.workers,
                         train_seeds=_ints(args.train_seeds), validation_seeds=_ints(args.validation_seeds),
                         candidate_ids=_strs(args.candidates), perturbation_ids=_strs(args.perturbations),
                         progress=_progress))
    elif args.cmd == "run-all":
        kw = {"meta_dir": args.meta_dir} if args.meta_dir else {}
        res = PL.run_all(args.config, args.out, n_players=args.players, workers=args.workers,
                         reference_seed=args.reference_seed, train_seeds=_ints(args.train_seeds),
                         validation_seeds=_ints(args.validation_seeds), candidate_ids=_strs(args.candidates),
                         perturbation_ids=_strs(args.perturbations), snapshot_dir=args.snapshot_dir,
                         snapshot_id=args.snapshot_id, revision=args.revision, progress=_progress, **kw)
        _print({k: {"status": v.get("status"), "wall_seconds": v.get("wall_seconds")}
                for k, v in res["stages"].items()})
    elif args.cmd == "report":
        from .report import build_report
        _print(build_report(args.dir))
    elif args.cmd == "app":
        from .app import build_dashboard
        _print(build_dashboard(args.dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
