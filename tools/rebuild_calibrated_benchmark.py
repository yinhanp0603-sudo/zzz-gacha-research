"""按冻结的当前配置重建L2/L3/搜索；保留目标目录的观测数据，验收后再发布。

build 在独立目录计算，允许使用该目录内身份匹配的已完成运行续跑。
publish 需要独立导出审核通过，归档旧衍生产物后替换，绝不调用prepare-observations。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from zzz_research import pipeline as PL
from zzz_research.analytics import search as SE
from zzz_research.config import resolve_config


def fingerprint(paths):
    result = {}
    for path in sorted(paths):
        with path.open("rb") as stream:
            result[str(path.relative_to(ROOT))] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def source_hashes():
    return fingerprint(list((ROOT / "src/zzz_research").rglob("*.py"))
                       + list((ROOT / "src/zzz_research/storage").glob("*.sql")))


def read(path):
    return json.loads(path.read_text("utf-8"))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def move_artifact(source, destination):
    # 全部位于同一workspace/卷，仅原子重命名；禁止shutil.move失败后退化为复制再删除。
    PL._replace_output(Path(source), Path(destination))


def build(stage, target, players, workers):
    entry = ROOT / "config/experiment.json"
    current = resolve_config(entry)
    stage.mkdir(parents=True, exist_ok=True)
    plan_path = stage / "refresh_plan.json"
    if plan_path.exists():
        plan = read(plan_path)
        require(plan["base_config_id"] == current.config_id and plan["players"] == players,
                "续跑配置或规模与冻结计划不同")
        require(plan["source_hashes"] == source_hashes(), "源码已变化，不能混合续跑")
    else:
        observations = target / "observations"
        manifest = read(observations / "observations_manifest.json")
        require(manifest.get("observed_data_available") is True, "缺少应保留的已导入Layer 1")
        frozen = stage / "config_snapshot"
        shutil.copytree(ROOT / "config", frozen)
        require(resolve_config(frozen / "experiment.json").config_id == current.config_id, "配置快照不一致")
        plan = {"base_config_id": current.config_id, "players": players,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "target": str(target), "stage": str(stage), "source_hashes": source_hashes(),
                "observation_fingerprints": fingerprint(p for p in observations.iterdir() if p.is_file()),
                "observation_stage": read(target / "pipeline_manifest.json")["stages"]["prepare_observations"]}
        PL.write_json(plan_path, plan)
    frozen_entry = stage / "config_snapshot/experiment.json"
    base = resolve_config(frozen_entry)
    require(base.config_id == plan["base_config_id"], "冻结配置已被改动，停止续跑")
    log = PL.StageLog(stage)
    validation = log.run("validate_config", lambda: PL.validate_config(frozen_entry))
    validation["execution_overrides"] = {"population.n_players": players}
    validation["execution_base_config_id"] = resolve_config(
        frozen_entry, overrides={"population.n_players": players}).config_id
    PL.write_json(stage / "validate.json", validation)
    log.run("build_benchmarks", lambda: PL.build_benchmarks(frozen_entry, stage / "benchmarks"))
    log.data["stages"]["prepare_observations"] = {**plan["observation_stage"], "preserved_existing": True}
    PL.write_json(log.path, log.data)
    seed = base.metadata["module_metadata"]["experiments"]["train_seeds"][0]
    refs = []
    for name in SE.reference_scenario_names(base):
        run = log.run(f"simulate_{name}", lambda name=name: PL.simulate(
            frozen_entry, stage / "reference", seed=seed, scenario=name, n_players=players, log_mode="full"))
        aggregate = log.run(f"aggregate_{name}", lambda run=run: PL.aggregate_run(
            run["path"], stage / "tables", run_id=run["run_id"], label=Path(run["path"]).stem))
        refs.append({**run, **{k: aggregate[k] for k in
                     ("total_revenue_fen", "core_retention_end", "retention_end_all", "core_initial")}})
        print(f"Reference complete: {name}; all={aggregate['retention_end_all']:.3%}; "
              f"core={aggregate['core_retention_end']:.3%}", flush=True)
    PL.write_json(stage / "reference_runs.json", refs)

    def progress(done, total):
        PL.write_json(stage / "search_progress.json", {"completed_new_runs": done, "total_new_runs": total})
        if done % 25 == 0 or done == total:
            print(f"Search complete: {done}/{total}", flush=True)

    log.run("search", lambda: PL.search(frozen_entry, stage / "search", n_players=players,
                                       workers=workers, progress=progress))
    require(resolve_config(entry).config_id == plan["base_config_id"], "计算期间当前配置发生变化，停止发布")
    require(source_hashes() == plan["source_hashes"], "计算期间源码发生变化，停止发布")
    plan.update(build_status="completed", built_at_utc=datetime.now(timezone.utc).isoformat())
    PL.write_json(plan_path, plan)
    print(json.dumps({"status": "built", "base_config_id": base.config_id, "stage": str(stage)}, ensure_ascii=False))


def relocate(value, stage, target):
    if isinstance(value, dict):
        return {k: relocate(v, stage, target) for k, v in value.items()}
    if isinstance(value, list):
        return [relocate(v, stage, target) for v in value]
    if isinstance(value, str) and value.startswith(str(stage)):
        return str(target) + value[len(str(stage)):]
    return value


def publish(stage, target):
    plan = read(stage / "refresh_plan.json")
    require(plan.get("build_status") == "completed", "计算尚未全部完成")
    require(resolve_config(ROOT / "config/experiment.json").config_id == plan["base_config_id"], "最新配置已变化")
    require(source_hashes() == plan["source_hashes"], "计算后源码已变化")
    require(resolve_config(stage / "config_snapshot/experiment.json").config_id == plan["base_config_id"],
            "冻结配置身份已变化")
    audit = read(stage / "verification/local_export_audit.json")
    require(audit.get("status") == "passed", "独立导出审核尚未通过")
    search_manifest = read(stage / "search/experiment_manifest.json")
    require(search_manifest["base_config_id"] == plan["base_config_id"], "搜索基线配置不一致")
    require(audit["scope"]["experiment_id"] == search_manifest["experiment_id"], "审核实验身份不一致")
    require(audit["scope"]["paired_summary_groups"] > 0, "最终配对摘要尚未审核")
    for name, item in audit["input_sha256"].items():
        path = stage / "search" / name
        require(hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], f"审核后输入已变化：{name}")
    lineage = read(stage / "verification/lineage_audit.json")
    require(lineage.get("status") == "passed" and lineage.get("base_config_id") == plan["base_config_id"],
            "逐库配置与运行身份核对尚未通过")
    for name, item in lineage.get("input_sha256", {}).items():
        require(hashlib.sha256(Path(name).read_bytes()).hexdigest() == item["sha256"],
                f"逐库审核后输入已变化：{name}")
    require(fingerprint(p for p in (target / "observations").iterdir() if p.is_file()) ==
            plan["observation_fingerprints"], "Layer 1文件已变化，暂停替换")
    # 改写生成清单中的本地路径；SQLite保存科学配置与身份，无需路径改写。
    reviewed_metadata = stage / "verification/reviewed_metadata"
    reviewed_metadata.mkdir(parents=True, exist_ok=True)
    for filename in ("pipeline_manifest.json", "reference_runs.json"):
        shutil.copy2(stage / filename, reviewed_metadata / filename)
        PL.write_json(stage / filename, relocate(read(stage / filename), stage, target))
    archive = target / "verification" / f"before_calibrated_{plan['base_config_id'][:12]}"
    archive.mkdir(parents=True, exist_ok=True)
    require(not any(archive.iterdir()), "归档目录已有数据，不能覆盖此前发布记录")
    names = ("benchmarks", "reference", "tables", "search", "report", "dashboard", "config_snapshot",
             "validate.json", "reference_runs.json", "pipeline_manifest.json")
    # 所有移动先验证绝对路径在当前workspace内，且只替换列明的衍生产物。
    for name in names:
        for path in (stage / name, target / name, archive / name):
            require(path.resolve().is_relative_to(ROOT), f"路径越界: {path}")
    moved_old, moved_new = [], []
    try:
        for name in names:
            if (target / name).exists():
                move_artifact(target / name, archive / name)
                moved_old.append(name)
            if (stage / name).exists():
                move_artifact(stage / name, target / name)
                moved_new.append(name)
    except BaseException:
        for name in reversed(moved_new):
            move_artifact(target / name, stage / name)
        for name in reversed(moved_old):
            move_artifact(archive / name, target / name)
        raise
    # 原标准审核名称移入历史目录，避免新报告旁仍出现指向e56配置的旧审核。
    for name in ("local_export_audit.json", "database_audit.json", "reference_ledger_audit.json",
                 "churn_sensitivity_audit.json", "presentation_refresh.json", "layer1_report_refresh.json"):
        old_audit = target / "verification" / name
        if old_audit.exists():
            old_target = archive / "verification" / name
            old_target.parent.mkdir(parents=True, exist_ok=True)
            move_artifact(old_audit, old_target)
    log = PL.StageLog(target)
    log.data["environment"] = {"python": platform.python_version(), "platform": platform.platform()}
    from zzz_research import report, app
    log.run("report", lambda: report.build_report(target))
    log.run("dashboard", lambda: app.build_dashboard(target))
    PL.write_json(target / "refresh_plan.json", plan)
    require(fingerprint(p for p in (target / "observations").iterdir() if p.is_file()) ==
            plan["observation_fingerprints"], "发布后Layer 1校验不一致")
    verification = target / "verification"
    published_audit = relocate(audit, stage, target)
    PL.write_json(verification / "calibrated_local_export_audit.json", published_audit)
    PL.write_json(verification / "local_export_audit.json", published_audit)
    published_lineage = relocate(lineage, stage, target)
    locations = {}
    for name, item in lineage.get("input_sha256", {}).items():
        original = Path(name)
        published = target / original.relative_to(stage) if original.is_relative_to(stage) else original
        if not published.exists():
            published = original
        retained = reviewed_metadata / original.name if original in (
            stage / "pipeline_manifest.json", stage / "reference_runs.json") else published
        require(hashlib.sha256(retained.read_bytes()).hexdigest() == item["sha256"],
                f"发布后已审核输入的保留副本不一致：{name}")
        locations[name] = {"retained_verified_copy": str(retained), "published_location": str(published),
                           "published_sha256": hashlib.sha256(published.read_bytes()).hexdigest()}
    published_lineage["verified_stage"] = str(stage)
    published_lineage["publication_input_locations"] = locations
    PL.write_json(verification / "database_audit.json", published_lineage)
    PL.write_json(verification / "calibrated_rebuild.json", {
        "status": "published", "base_config_id": plan["base_config_id"], "players": plan["players"],
        "observation_files_unchanged": True, "archive": str(archive),
        "source_hashes": plan["source_hashes"], "published_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": str(stage), "config_snapshot": str(target / "config_snapshot/experiment.json")})
    print(json.dumps({"status": "published", "target": str(target), "archive": str(archive)}, ensure_ascii=False))


def verify(stage):
    from zzz_research.analytics.paired import paired_summary
    from zzz_research.report import read_csv
    plan = read(stage / "refresh_plan.json")
    require(plan.get("build_status") == "completed", "计算尚未全部完成")
    PL.write_csv(stage / "search/paired_differences.csv", paired_summary(read_csv(stage / "search/search_results.csv")))
    subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools/verify_benchmark_exports.py"), str(stage)],
                   cwd=ROOT, check=True)
    subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools/verify_calibrated_lineage.py"), str(stage)],
                   cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("build", "verify", "publish"), default="build")
    parser.add_argument("--players", type=int, default=200)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--target", type=Path, default=ROOT / "outputs/benchmark_n200")
    parser.add_argument("--stage", type=Path, default=None)
    args = parser.parse_args()
    current = resolve_config(ROOT / "config/experiment.json")
    stage = (args.stage or ROOT / "outputs" / f"benchmark_n200_refresh_{current.config_id[:12]}").resolve()
    target = args.target.resolve()
    require(stage != target and stage.is_relative_to(ROOT) and target.is_relative_to(ROOT), "暂存与目标路径不合法")
    if args.phase == "build":
        build(stage, target, args.players, args.workers)
    elif args.phase == "verify":
        verify(stage)
    else:
        publish(stage, target)


if __name__ == "__main__":
    main()
