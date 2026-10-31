"""Validate transferred run databases and rebuild summaries with simulation disabled.

Uses the production read-only validator, while recording exactly which databases
were checked. Missing/invalid caches cause a hard stop instead of a new run.
"""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import time

from zzz_research import pipeline as PL
from zzz_research.analytics import search as SE


def rebuild(root):
    root = root.resolve()
    verification = root / "verification"
    backup = verification / "cloud_delivery_before_rebuild"
    backup.mkdir(parents=True, exist_ok=True)
    for rel in ("pipeline_manifest.json", "reference_runs.json", "report/REPORT.md", "dashboard/index.html",
                "search/experiment_manifest.json", "search/search_results.csv", "search/seed_robustness.csv",
                "search/rank_stability.csv", "search/parameter_sensitivity.csv"):
        source = root / rel
        target = backup / rel
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    checked = {}
    errors = []
    original_validate = SE._validate_completed
    original_execute = SE._execute_one
    original_simulate = SE.run_simulation

    def validate(conn, run_id, **kwargs):
        try:
            key = original_validate(conn, run_id, **kwargs)
        except Exception as exc:
            errors.append(dict(run_id=run_id, error=f"{type(exc).__name__}: {exc}"))
            raise
        if run_id not in checked:
            row = conn.execute(
                "SELECT config_id,master_seed,environment_json,algorithm_versions_json FROM run_manifest WHERE run_id=?",
                (run_id,)).fetchone()
            mode = conn.execute("SELECT log_mode FROM run_attempt WHERE run_id=? ORDER BY attempt_id DESC LIMIT 1",
                                (run_id,)).fetchone()[0]
            checked[run_id] = dict(config_id=row[0], seed=row[1], log_mode=mode,
                                   environment=json.loads(row[2]), algorithm_versions=json.loads(row[3]))
            if len(checked) % 50 == 0:
                print(f"Validated search databases: {len(checked)}/650", flush=True)
        return key

    def forbid_simulation(*args, **kwargs):
        raise RuntimeError("Reuse-only rebuild: a cache is missing or invalid; simulation is disabled")

    started = time.perf_counter()
    try:
        SE._validate_completed = validate
        SE._execute_one = forbid_simulation
        SE.run_simulation = forbid_simulation
        summary = PL.search("config/experiment.json", root / "search", n_players=200, workers=1)
    finally:
        SE._validate_completed = original_validate
        SE._execute_one = original_execute
        SE.run_simulation = original_simulate
    if len(checked) != 650 or summary["n_unique_runs"] != 650 or errors:
        raise RuntimeError(f"Expected 650 validated reused runs: checked={len(checked)}, errors={errors[:3]}")
    refs = json.loads((root / "reference_runs.json").read_text("utf-8"))
    reference_checks = []
    for ref in refs:
        db = root / "reference" / Path(ref["path"]).name
        conn = SE._read_only(db)
        try:
            original_validate(conn, ref["run_id"], expected_key=(ref["config_id"], ref["seed"]), required_log_mode="full")
            config = json.loads(conn.execute("SELECT config_json FROM run_manifest WHERE run_id=?", (ref["run_id"],)).fetchone()[0])
            obj = SE.run_objectives(SE._RunReader(conn), ref["run_id"], config["modules"]["population"]["core_retention_tier"])
            if obj.total_revenue_fen != ref["total_revenue_fen"] or obj.core_retention_end != ref["core_retention_end"]:
                raise RuntimeError(f"Reference export differs from database: {ref['scenario']}")
            reference_checks.append(dict(scenario=ref["scenario"], run_id=ref["run_id"],
                                         total_revenue_fen=obj.total_revenue_fen,
                                         core_retention_end=obj.core_retention_end, log_mode="full"))
        finally:
            conn.close()
    con = sqlite3.connect((root / "search/search.sqlite").as_uri() + "?mode=ro", uri=True)
    try:
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"Merged database foreign keys failed: {fk[:3]}")
        counts = {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("run_manifest", "agg_player_period", "fact_player_lifecycle",
                                "agg_search_results", "agg_seed_robustness", "agg_rank_stability",
                                "agg_parameter_sensitivity")}
    finally:
        con.close()
    with (root / "search/search_results.csv").open(encoding="utf-8", newline="") as f:
        rebuilt = list(csv.DictReader(f))
    with (backup / "search/search_results.csv").open(encoding="utf-8", newline="") as f:
        cloud = list(csv.DictReader(f))
    key = lambda r: (r["candidate_id"], r["perturbation_id"], r["split"], r["replicate_id"])
    if {key(r): r for r in rebuilt} != {key(r): r for r in cloud}:
        raise RuntimeError("Rebuilt per-run objective exports differ from the delivered cloud table")
    result = dict(status="passed", verified_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  search_databases_verified=len(checked), reference_databases_verified=len(reference_checks),
                  simulations_executed=0, log_modes=dict(Counter(r["log_mode"] for r in checked.values())),
                  foreign_key_errors=0, quick_check="ok", config_and_run_identity_checks="passed",
                  expected_player_period_counts="passed", rebuilt_objectives_equal_cloud_export=True,
                  summary=summary, merged_table_counts=counts, reference_checks=reference_checks,
                  elapsed_seconds=time.perf_counter() - started, original_runs=checked,
                  limits=["Search summary runs do not contain per-pull/asset details.",
                          "These are restored 200-player runs, not new simulations or a 3000-player validation."])
    receipt = verification / "database_audit.json"
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "original_runs"}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", nargs="?", type=Path, default=Path("outputs/benchmark_n200"))
    rebuild(p.parse_args().root)
