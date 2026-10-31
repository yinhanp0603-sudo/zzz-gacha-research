"""M5 CLI、流水线、报告与看板：端到端小规模运行；报告不空造未运行阶段；产物格式有效。"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from zzz_research import __main__ as CLI
from zzz_research import pipeline as PL
from zzz_research.app import build_dashboard
from zzz_research.config import resolve_config
from zzz_research.report import build_report, heatmap, line_chart, scatter_chart

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "config" / "experiment.json"


@pytest.fixture(scope="module")
def pipeline_out(tmp_path_factory):
    out = tmp_path_factory.mktemp("runall")
    cfg = resolve_config(ENTRY)
    seeds = cfg.metadata["module_metadata"]["experiments"]
    rc = CLI.main(["run-all", "--config", str(ENTRY), "--out", str(out), "--players", "3", "--workers", "1",
                   "--train-seeds", str(seeds["train_seeds"][0]), "--validation-seeds", str(seeds["validation_seeds"][0]),
                   "--candidates", "L1_A1,L2_A1.6", "--perturbations", "P0,gap_weight_x0.75",
                   "--meta-dir", str(ROOT / "data" / "raw" / "SRC_SHIYU_meta_d930cec50bfd")])
    assert rc == 0
    return out


def test_validate_config_cli(capsys):
    assert CLI.main(["validate-config", "--config", str(ENTRY)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["config_id"] == resolve_config(ENTRY).config_id and out["status"] == "ok"


def test_run_all_stages_completed(pipeline_out):
    man = json.loads((pipeline_out / "pipeline_manifest.json").read_text("utf-8"))
    assert man["stages"] and all(s["status"] == "completed" for s in man["stages"].values())
    for name in ("validate_config", "build_benchmarks", "prepare_observations", "search", "report", "dashboard"):
        assert name in man["stages"]
    refs = json.loads((pipeline_out / "reference_runs.json").read_text("utf-8"))
    cfg = resolve_config(ENTRY)
    assert sorted(r["scenario"] for r in refs) == sorted(cfg.module("scenarios")["reference_scenarios"])
    assert len({r["config_id"] for r in refs}) == len(refs)  # 不同情景 = 不同科学配置


def test_report_is_honest_about_scope(pipeline_out):
    text = (pipeline_out / "report" / "REPORT.md").read_text("utf-8")
    assert "reduced_scale" in text and "population.n_players" in text
    assert "原始战斗记录未导入" in text          # L1 未导入时不空造结果
    assert "模拟" in text and "假设" in text
    for line in text.splitlines():
        if line.startswith("![") and "figures/" in line:
            assert (pipeline_out / "report" / line.split("(")[1].rstrip(")")).exists()


def test_figures_are_well_formed_svg(pipeline_out):
    figs = list((pipeline_out / "report" / "figures").glob("*.svg"))
    assert len(figs) >= 6
    for f in figs:
        root = ET.fromstring(f.read_text("utf-8"))
        assert root.tag.endswith("svg")
        title = root.find("{http://www.w3.org/2000/svg}text").text
        if f.stem.startswith(("ref_", "cohort", "candidates")):
            assert "模拟" in title                   # Layer 3 图标题含“模拟”


def test_dashboard_four_tabs(pipeline_out):
    html = (pipeline_out / "dashboard" / "index.html").read_text("utf-8")
    for label in ("数据与口径", "战斗对照", "行为情景", "候选与稳健性"):
        assert label in html
    assert html.count('role="tabpanel"') == 4
    assert "<script src=" not in html               # 自包含，无外部依赖


def test_report_without_search_marks_not_run(tmp_path):
    PL.build_benchmarks(ENTRY, tmp_path / "benchmarks")
    PL.write_json(tmp_path / "validate.json", PL.validate_config(ENTRY))
    build_report(tmp_path)
    text = (tmp_path / "report" / "REPORT.md").read_text("utf-8")
    assert "_未运行_" in text and "累计现金" not in text.split("## 4.")[1].split("## 5.")[0]
    build_dashboard(tmp_path)


def test_prepare_observations_cli_with_snapshot(tmp_path, shiyu_snapshot):
    out = tmp_path / "obs"
    rc = CLI.main(["prepare-observations", "--out", str(out), "--meta-dir", str(shiyu_snapshot["root"]),
                   "--snapshot-dir", str(shiyu_snapshot["snapshot"]), "--snapshot-id", "SNAP_T", "--revision", "rev"])
    assert rc == 0
    for f in ("obs_coverage.csv", "appearance.csv", "results_by_stage.csv", "exclusions.csv", "quality_issues.json"):
        assert (out / f).exists()
    assert json.loads((out / "quality_issues.json").read_text("utf-8")) == []
    with pytest.raises(PL.PipelineError):
        PL.prepare_observations(tmp_path / "o2", meta_dir=shiyu_snapshot["root"],
                                snapshot_dir=shiyu_snapshot["snapshot"])  # 缺固定修订 → 拒绝


def test_simulate_and_aggregate_cli(tmp_path):
    cfg = resolve_config(ENTRY)
    seed = cfg.metadata["module_metadata"]["experiments"]["train_seeds"][0]
    assert CLI.main(["simulate", "--config", str(ENTRY), "--replicate", str(seed), "--players", "2",
                     "--out", str(tmp_path / "runs")]) == 0
    db = next((tmp_path / "runs").glob("*.sqlite"))
    assert not any(p.name.endswith(".tmp.sqlite") for p in (tmp_path / "runs").iterdir())  # 完成后发布
    assert CLI.main(["aggregate", "--db", str(db), "--out", str(tmp_path / "tables")]) == 0
    assert list((tmp_path / "tables").glob("*_cohort_availability.csv"))


def test_svg_helpers_handle_empty_and_null():
    for svg in (line_chart({"a": [(1, None), (2, 1.0), (3, None)]}, title="t", xlabel="x", ylabel="y"),
                scatter_chart({}, title="t", xlabel="x", ylabel="y"),
                heatmap(["r"], ["c"], {("r", "c"): None}, title="t")):
        ET.fromstring(svg)
