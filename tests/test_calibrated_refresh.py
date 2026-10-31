"""仅用临时文件检验标定产物发布的拒绝与回滚，不运行模拟。"""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("calibrated_refresh_tool", ROOT / "tools/rebuild_calibrated_benchmark.py")
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


def _json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def publication(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, "ROOT", tmp_path)
    monkeypatch.setattr(refresh, "source_hashes", lambda: {})
    monkeypatch.setattr(refresh, "resolve_config", lambda path: SimpleNamespace(config_id="BASE"))
    target, stage = tmp_path / "target", tmp_path / "stage"
    observations = target / "observations"
    observations.mkdir(parents=True)
    (observations / "immutable.csv").write_bytes(b"retained real-data substitute fixture")
    _json(stage / "refresh_plan.json", {"build_status": "completed", "base_config_id": "BASE", "players": 200,
          "source_hashes": {}, "observation_fingerprints": refresh.fingerprint(observations.iterdir())})
    _json(stage / "search/experiment_manifest.json", {"base_config_id": "BASE", "experiment_id": "E"})
    _json(stage / "verification/local_export_audit.json", {"status": "passed", "input_sha256": {},
          "scope": {"experiment_id": "E", "paired_summary_groups": 1}})
    _json(stage / "verification/lineage_audit.json", {"status": "passed", "base_config_id": "BASE"})
    _json(stage / "pipeline_manifest.json", {"stages": {}})
    _json(stage / "reference_runs.json", [])
    for root, value in ((target, "old"), (stage, "new")):
        for name in ("benchmarks", "reference"):
            p = root / name / "proof.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(value, encoding="utf-8")
    return stage, target


def test_audit_cannot_authorize_changed_input(publication):
    stage, target = publication
    audit_path = stage / "verification/local_export_audit.json"
    audit = json.loads(audit_path.read_text())
    p = stage / "search/search_results.csv"
    p.write_bytes(b"old reviewed content")
    audit["input_sha256"][p.name] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    _json(audit_path, audit)
    p.write_bytes(b"changed after review")
    with pytest.raises(RuntimeError, match="审核后输入已变化"):
        refresh.publish(stage, target)
    assert (target / "benchmarks/proof.txt").read_text() == "old"


def test_changed_layer1_is_never_replaced(publication):
    stage, target = publication
    source = target / "observations/immutable.csv"
    source.write_bytes(b"new independent observation update")
    with pytest.raises(RuntimeError, match="Layer 1文件已变化"):
        refresh.publish(stage, target)
    assert source.read_bytes() == b"new independent observation update"
    assert (target / "benchmarks/proof.txt").read_text() == "old"


def test_mid_move_failure_rolls_back_only_derived_artifacts(publication, monkeypatch):
    stage, target = publication
    move = refresh.move_artifact
    calls = []

    def fail_once(source, destination):
        calls.append(source)
        if len(calls) == 3:
            raise PermissionError("simulated transient directory lock")
        return move(source, destination)

    monkeypatch.setattr(refresh, "move_artifact", fail_once)
    with pytest.raises(PermissionError):
        refresh.publish(stage, target)
    assert (target / "benchmarks/proof.txt").read_text() == "old"
    assert (target / "reference/proof.txt").read_text() == "old"
    assert (stage / "benchmarks/proof.txt").read_text() == "new"
    assert (target / "observations/immutable.csv").read_bytes() == b"retained real-data substitute fixture"
