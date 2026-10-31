"""独立核对完成后更新阶段记录，执行 report/app CLI，并保存刷新证据。"""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


root = Path(__file__).resolve().parents[1]
out = root / "outputs/benchmark_n200"
verification = out / "verification"
audit = json.loads((verification / "layer1_import_verification.json").read_text("utf-8"))
manifest = json.loads((out / "observations/observations_manifest.json").read_text("utf-8"))
if not audit["all_checks_passed"] or not manifest["observed_data_available"]:
    raise RuntimeError("导入独立核对未通过，不更新报告")


def hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (out / "report/figures").glob("*.svg")}


before = hashes()
started = time.perf_counter()
pipeline_path = out / "pipeline_manifest.json"
pipeline = json.loads(pipeline_path.read_text("utf-8"))
backup = verification / "before_layer1_import_20261028/pipeline_manifest.json"
if not backup.exists():
    shutil.copyfile(pipeline_path, backup)
start_at = datetime.fromisoformat(manifest["started_at"])
end_at = datetime.fromisoformat(manifest["ended_at"])
pipeline["stages"]["prepare_observations"] = {
    "status": "completed", "data_status": manifest["status"],
    "started_at": manifest["started_at"], "ended_at": manifest["ended_at"],
    "wall_seconds": (end_at - start_at).total_seconds(),
    **{k: manifest[k] for k in ("n_collection_periods", "n_periods_complete", "n_battles", "n_eligible",
                                "n_scopes", "n_import_batches", "snapshot_id", "revision", "data_license",
                                "quality_issues", "path")},
}
pipeline_path.write_text(json.dumps(pipeline, ensure_ascii=False, indent=2), encoding="utf-8")
for command in ("report", "app"):
    subprocess.run([sys.executable, "-X", "utf8", "-m", "zzz_research", command, "--dir", str(out)],
                   cwd=root, check=True)

report = out / "report/REPORT.md"
text = report.read_text("utf-8")
section2 = text.split("## 2.", 1)[1].split("## 3.", 1)[0]
if "真实公开战斗记录已导入" not in section2 or f"{manifest['n_battles']:,}" not in section2:
    raise RuntimeError("报告第 2 节未呈现当前导入结果")
if "原始战斗记录未导入" in section2:
    raise RuntimeError("报告仍显示旧的未导入提示")
after = hashes()
changed = [name for name, digest in before.items() if after.get(name) != digest]
result = {"updated_at_utc": datetime.now(timezone.utc).isoformat(),
          "revision": manifest["revision"], "n_battles": manifest["n_battles"],
          "n_eligible": manifest["n_eligible"], "quality_issues": manifest["quality_issues"],
          "report": str(report), "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
          "dashboard": str(out / "dashboard/index.html"),
          "existing_figures": len(before), "changed_existing_figures": changed,
          "figure_sha256": after, "elapsed_seconds": time.perf_counter() - started,
          "simulations_executed": 0, "browser_rendering": "not_verified"}
(verification / "layer1_report_refresh.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in result.items() if k != "figure_sha256"}, ensure_ascii=False, indent=2))
