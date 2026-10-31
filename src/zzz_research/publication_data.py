"""Read the completed studies used by the integrated report and dashboard."""

from __future__ import annotations

import csv
import json
import re
import shutil
import sqlite3
from pathlib import Path

from .archetype_presentation import load_archetype_presentation
from .pipeline import from_project

PUBLICATION_FILE = "publication_manifest.json"
FIGURE_NAMES = (
    "rank_stability", "axis_cash_ci", "axis_retention_ci",
    "frontier_inclusion", "support_cash_decomposition",
)
FIXED_FILES = (
    "l2_reference_panel", "l2_reference_lifetimes", "l2_support_pair_effects",
    "run_summary", "period_outcomes", "spending_outcomes", "parameter_registry",
)
EMPIRICAL_TABLES = {
    "real_dps_longevity": "empirical_real_dps_longevity.csv",
    "support_event_window": "empirical_support_event_window.csv",
    "support_lift": "empirical_knob2_support_lift.csv",
    "support_attach": "empirical_support_attach.csv",
    "support_archetypes": "empirical_support_archetypes.csv",
    "anomaly_ecosystem": "empirical_anomaly_ecosystem.csv",
    "room_archetype_share": "empirical_room_archetype_share.csv",
}
EXTERNAL_TABLES = {
    "banner_revenue": "external_gacha_banner_revenue.csv",
    "banner_category_ratios": "external_banner_category_ratios.csv",
    "rngmoe_banners": "external_rngmoe_banner_stats.csv",
    "rngmoe_pity_curve": "external_rngmoe_pity_curve.csv",
    "sim_benchmarks": "external_sim_benchmarks.csv",
}
EXTERNAL_SOURCES = "external_source_manifest.json"
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _typed_value(key: str, value: str):
    """Keep identities and serialized JSON intact while typing measured values."""
    if value == "":
        return None
    if key.endswith(("_id", "_json")) or key in {"unit", "investment_plan"}:
        return value
    if value in {"True", "False"}:
        return value == "True"
    if _NUMBER.fullmatch(value):
        return float(value) if any(c in value.lower() for c in ".e") else int(value)
    return value


def read_typed_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return [{key: _typed_value(key, value) for key, value in row.items()}
                for row in csv.DictReader(stream)]


def load_publication_data(out_dir: Path | str) -> dict:
    """Load local summary artifacts without opening a simulation or observation DB."""
    out = Path(out_dir)
    publication = _read_json(out / PUBLICATION_FILE)
    tables = {}
    for name, study in publication["studies"].items():
        directory = out / study["tables"]
        tables[name] = {path.stem: read_typed_csv(path)
                        for path in sorted(directory.glob("*.csv"))}
    primary = publication["studies"][publication["primary_study"]]
    manifest = _read_json(out / primary["manifest"])
    if manifest["status"] != "completed":
        raise ValueError("当前主实验尚未完成，不能发布其结论")
    snapshot = _read_json(out / publication["config_snapshot"])
    if snapshot["config_id"] != manifest["base_config_id"]:
        raise ValueError("发布配置与主实验配置不一致")
    science = snapshot["science"]
    registered = {**publication.get("empirical_tables", {}), **publication.get("external_tables", {})}
    evidence = {name: read_typed_csv(out / path) for name, path in registered.items() if (out / path).is_file()}
    sources = publication.get("external_sources")
    return {
        "tables": tables,
        "evidence": evidence,
        "external_sources": _read_json(out / sources)["sources"] if sources and (out / sources).is_file() else [],
        "manifest": manifest,
        "publication_manifest": publication,
        "observations": _read_json(out / "observations/observations_manifest.json"),
        "layer1": load_archetype_presentation(out),
        "figures": {path.stem: path for path in (out / "report/figures").glob("*.svg")},
        "config_science": science,
        "combat_pairs": science["modules"]["combat"]["research_v2"]["support_pairs"],
    }


def _copy_fixed_table(source: Path, target: Path) -> int:
    """Publish the registered L2 reference condition; keep the full grid at source."""
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        with target.open("w", encoding="utf-8", newline="") as dest:
            writer = csv.DictWriter(dest, fieldnames=reader.fieldnames)
            writer.writeheader()
            count = 0
            for row in reader:
                if (row["policy_id"] == "L1_A1" and row["combat_variant"] == "v2"
                        and row["horizon"] == "20"):
                    writer.writerow(row)
                    count += 1
    return count


def prepare_publication(project_dir: Path | str, out_dir: Path | str) -> dict:
    """Collect completed exports and the recorded science snapshot for publication."""
    project, out = Path(project_dir).resolve(), Path(out_dir).resolve()
    source_roots = {
        "n1000": project / "outputs/research_v2_stability_n1000",
        "n200": project / "outputs/research_v2_stability",
        "fixed": project / "outputs/research_v2",
        "ablation": project / "outputs/research_v2_behavior_ablation",
        "archetype": project / "outputs/research_v2_archetype",
    }
    ablation = _read_json(source_roots["ablation"] / "experiment_manifest.json")
    if ablation["status"] != "completed" or ablation["completed_runs"] != ablation["planned_runs"]:
        raise ValueError("行为结构消融未全部完成")
    archetype = _read_json(source_roots["archetype"] / "experiment_manifest.json")
    if archetype["status"] != "completed" or archetype["completed_runs"] != len(archetype["cells"]) * len(archetype["seeds"]):
        raise ValueError("体系实验未全部完成")
    missing = [path for path in (*EMPIRICAL_TABLES.values(), *EXTERNAL_TABLES.values(), EXTERNAL_SOURCES)
               if not (out / path).is_file()]
    if missing:
        raise FileNotFoundError(f"发布目录缺少实证或外部参照表：{missing}")
    primary = _read_json(source_roots["n1000"] / "experiment_manifest.json")
    if primary["status"] != "completed" or primary["completed_runs"] != primary["planned_runs"]:
        raise ValueError("千人主实验未全部完成")
    if not (out / "observations/observations_manifest.json").is_file():
        raise FileNotFoundError("发布目录须保留已有 Layer 1 观测结果")
    for old_name, backup_name in (("report/REPORT.md", "report/BASELINE_REPORT.md"),
                                  ("REPORT.md", "BASELINE_REPORT.md"),
                                  ("dashboard/index.html", "dashboard/baseline.html")):
        old, backup = out / old_name, out / backup_name
        if old.exists() and not backup.exists() and not (out / PUBLICATION_FILE).exists():
            shutil.copy2(old, backup)

    studies = {}
    for name, source in source_roots.items():
        target = out / "research" / name
        table_dir = target / "tables"
        table_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "experiment_manifest.json", target / "experiment_manifest.json")
        counts = {}
        sources = ([source / "tables" / f"{stem}.csv" for stem in FIXED_FILES]
                   if name == "fixed" else sorted((source / "tables").glob("*.csv")))
        for path in sources:
            destination = table_dir / path.name
            if name == "fixed" and path.stem.startswith("l2_"):
                counts[path.stem] = _copy_fixed_table(path, destination)
            else:
                shutil.copy2(path, destination)
        studies[name] = {
            "source_output": source.relative_to(project).as_posix(),
            "tables": table_dir.relative_to(out).as_posix(),
            "manifest": (target / "experiment_manifest.json").relative_to(out).as_posix(),
        }
        if name == "fixed":
            studies[name].update({"reference_condition": {
                "policy_id": "L1_A1", "combat_variant": "v2", "horizon": 20,
            }, "reference_rows": counts})

    base = next(row for row in primary["runs"]
                if row["policy_id"] == "L1_A1" and row["seed"] == primary["train_seeds"][0])
    database = from_project(base["database"])
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        config_id, config_json = connection.execute(
            "SELECT config_id, config_json FROM run_manifest WHERE run_id=?", (base["run_id"],)
        ).fetchone()
    if config_id != primary["base_config_id"]:
        raise ValueError("基线运行与主实验配置身份不一致")
    snapshot = {"config_id": config_id, "science": json.loads(config_json)}
    config_path = out / "research/config_science.json"
    config_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    figure_dir = out / "report/figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    for name in FIGURE_NAMES:
        shutil.copy2(source_roots["n1000"] / f"figures/{name}.svg", figure_dir / f"v2_{name}.svg")
    publication = {
        "publication_version": "integrated_v2",
        "primary_study": "n1000",
        "base_config_id": config_id,
        "studies": studies,
        "empirical_tables": EMPIRICAL_TABLES,
        "external_tables": EXTERNAL_TABLES,
        "external_sources": EXTERNAL_SOURCES,
        "evidence_levels": {
            "L1": "配队流派、机制演替与主核—插件共现：完整三人与危局精确池的描述统计",
            "L1.5": "专拐与成绩的条件关联：固定主核投资档位与第三人后的分位数对照，非因果净效应",
            "L2": "角色保值寿命与售后恢复：真实主C经验轨迹＋固定合成参考队受控对照",
            "L3": "囤抽、付费、停氪、退坑与长期现金：显式行为函数下的反事实情景与结构消融",
        },
        "config_snapshot": config_path.relative_to(out).as_posix(),
        "observation_database": "observations/observations.sqlite",
        "layer1_reused": True,
        "legacy_report": "report/BASELINE_REPORT.md",
        "legacy_dashboard": "dashboard/baseline.html",
        "horizon_definition": "10期为同一20期物理运行的前缀，两个窗口不是独立样本。",
        "cash_unit": "CNY",
        "timing": {
            "summed_run_wall_seconds": sum(row["wall_seconds"] for row in primary["runs"]),
            "physical_runs": primary["completed_runs"],
            "new_runs": primary["new_runs"],
            "reused_runs": primary["reused_runs"],
            "log_mode": "summary",
        },
    }
    (out / PUBLICATION_FILE).write_text(json.dumps(publication, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return publication
