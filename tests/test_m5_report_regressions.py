"""呈现层的配对解释、跨实验过滤和旧产物隔离。"""

import csv
import json

from zzz_research.app import build_dashboard
from zzz_research.report import build_report, observations_available, search_records


def _json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _search(out):
    _json(out / "search/experiment_manifest.json", dict(
        experiment_id="E", candidates=["A", "B"], train_seeds=[], validation_seeds=[1, 2, 3],
        perturbations=["P0"], common_overrides={}, scale="fixture",
        summary=dict(logical_runs=6, unique_runs=6),
    ))
    rows = []
    for c, delta in (("A", 0), ("B", 3)):
        for seed, revenue in enumerate((100, 108, 92), 1):
            rows.append(dict(experiment_id="E", candidate_id=c, perturbation_id="P0", split="validation",
                             master_seed=seed, replicate_id=f"seed:{seed}", total_revenue_fen=revenue + delta,
                             delta_revenue_vs_base=delta, delta_retention_vs_base=0))
    _csv(out / "search/search_results.csv", rows)


def test_report_uses_paired_uncertainty_and_discloses_missing_databases(tmp_path):
    _search(tmp_path)
    build_report(tmp_path)
    text = (tmp_path / "report/REPORT.md").read_text("utf-8")
    assert "CRN同种子配对差" in text and "配对差SD" in text
    assert "差异小于种子波动时视为无法区分" not in text
    assert "无法在本机逐库复查" in text
    with (tmp_path / "search/paired_differences.csv").open(encoding="utf-8", newline="") as f:
        b = next(r for r in csv.DictReader(f) if r["candidate_id"] == "B")
    assert float(b["delta_revenue_mean"]) == 3 and float(b["delta_revenue_sd"]) == 0
    assert int(b["delta_revenue_n"]) == 3


def test_failed_search_does_not_display_stale_results(tmp_path):
    _search(tmp_path)
    _json(tmp_path / "pipeline_manifest.json", {"stages": {"search": {"status": "failed"}}})
    fig = tmp_path / "report/figures/candidates.svg"
    fig.parent.mkdir(parents=True)
    fig.write_text('<svg><text>STALE_CANDIDATES</text></svg>', encoding="utf-8")
    build_report(tmp_path)
    build_dashboard(tmp_path)
    assert search_records(tmp_path, "search_results.csv") == []
    assert "STALE_CANDIDATES" not in (tmp_path / "dashboard/index.html").read_text("utf-8")
    assert "CRN同种子配对差" not in (tmp_path / "report/REPORT.md").read_text("utf-8")


def test_search_rows_are_bound_to_current_experiment(tmp_path):
    _search(tmp_path)
    _csv(tmp_path / "search/rank_stability.csv", [
        dict(experiment_id="E", rho_rev=0.4), dict(experiment_id="OLD", rho_rev=1),
    ])
    assert search_records(tmp_path, "rank_stability.csv") == [dict(experiment_id="E", rho_rev="0.4")]


def test_metadata_status_overrides_stale_observation_csv(tmp_path):
    _json(tmp_path / "observations/observations_manifest.json",
          {"status": "metadata_only", "observed_data_available": False})
    _csv(tmp_path / "observations/obs_coverage.csv", [dict(combat_season="STALE_SEASON")])
    assert not observations_available(tmp_path)
    build_report(tmp_path)
    build_dashboard(tmp_path)
    for path in (tmp_path / "report/REPORT.md", tmp_path / "dashboard/index.html"):
        assert "STALE_SEASON" not in path.read_text("utf-8")


def test_text_only_repair_preserves_existing_figures(tmp_path):
    _search(tmp_path)
    fig = tmp_path / "report/figures/candidates.svg"
    fig.parent.mkdir(parents=True)
    fig.write_text('<svg><text>UNCHANGED_FIGURE</text></svg>', encoding="utf-8")
    old = fig.read_bytes()
    build_report(tmp_path, rebuild_figures=False)
    assert fig.read_bytes() == old
