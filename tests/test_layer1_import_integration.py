"""真实导入接口的许可传播、空输入拒绝与报告披露；仅使用临时合成夹具。"""
import json
import sqlite3

import pytest

from zzz_research.__main__ import main
from zzz_research.report import build_report
from zzz_research import pipeline as PL


def test_cli_import_propagates_declared_dataset_license_and_report_counts(tmp_path, shiyu_snapshot):
    out = tmp_path / "project"
    obs = out / "observations"
    assert main(["prepare-observations", "--out", str(obs),
                 "--meta-dir", str(shiyu_snapshot["root"]),
                 "--snapshot-dir", str(shiyu_snapshot["snapshot"]),
                 "--snapshot-id", "DECLARED_LICENSE_FIXTURE", "--revision", "fixture_revision",
                 "--data-license", "mit"]) == 0
    manifest = json.loads((obs / "observations_manifest.json").read_text("utf-8"))
    assert manifest["data_license"] == "mit" and manifest["n_import_batches"] > 0
    with sqlite3.connect(obs / "observations.sqlite") as conn:
        actual = conn.execute("SELECT COUNT(*), SUM(is_eligible) FROM obs_battle").fetchone()
        licenses = conn.execute("SELECT DISTINCT license FROM source_manifest WHERE revision='fixture_revision'").fetchall()
    assert actual == (manifest["n_battles"], manifest["n_eligible"])
    assert licenses == [("mit",)]
    build_report(out)
    text = (out / "report/REPORT.md").read_text("utf-8")
    assert f"共 {actual[0]:,} 条队伍战斗记录" in text
    assert "fixture_revision" in text and "无法代表全部玩家" in text


def test_empty_snapshot_cannot_be_reported_as_imported(tmp_path):
    snapshot = tmp_path / "empty"
    snapshot.mkdir()
    out = tmp_path / "observations"
    with pytest.raises(ValueError):
        main(["prepare-observations", "--out", str(out), "--snapshot-dir", str(snapshot),
              "--snapshot-id", "EMPTY_FIXTURE", "--revision", "fixture_revision"])
    manifest = json.loads((out / "observations_manifest.json").read_text("utf-8"))
    assert manifest["status"] == "failed" and manifest["observed_data_available"] is False


@pytest.mark.parametrize("kind", ["json", "csv"])
def test_transient_windows_output_lock_keeps_old_file_until_atomic_publish(tmp_path, monkeypatch, kind):
    path = tmp_path / f"progress.{kind}"
    path.write_text("previous complete output", encoding="utf-8")
    replace = PL.os.replace
    attempts = []

    def transient_lock(source, target):
        attempts.append(1)
        if len(attempts) == 1:
            assert path.read_text("utf-8") == "previous complete output"
            raise PermissionError("simulated Windows sharing violation")
        return replace(source, target)

    monkeypatch.setattr(PL.os, "replace", transient_lock)
    if kind == "json":
        PL.write_json(path, {"status": "completed"})
        assert json.loads(path.read_text("utf-8"))["status"] == "completed"
    else:
        PL.write_csv(path, [{"status": "completed"}])
        assert "completed" in path.read_text("utf-8")
    assert len(attempts) == 2 and not path.with_suffix(path.suffix + ".tmp").exists()
