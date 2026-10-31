"""Publication uses exported evidence and retains the recorded scientific identity."""

import json
from pathlib import Path

import pytest

from zzz_research.publication_data import load_publication_data, read_typed_csv


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _publication(tmp_path):
    _json(tmp_path / "publication_manifest.json", {
        "primary_study": "n1000",
        "studies": {"n1000": {"manifest": "research/experiment_manifest.json",
                                "tables": "research/tables"}},
        "config_snapshot": "research/science.json",
    })
    _json(tmp_path / "research/experiment_manifest.json", {
        "status": "completed", "base_config_id": "012345",
    })
    _json(tmp_path / "research/science.json", {
        "config_id": "012345",
        "science": {"modules": {"combat": {"research_v2": {"support_pairs": {
            "DPS_01": {"S_A": {"net_gain": 0.23}},
        }}}}},
    })
    _json(tmp_path / "observations/observations_manifest.json", {
        "observed_data_available": False,
    })
    table = tmp_path / "research/tables/grid_summary.csv"
    table.parent.mkdir()
    table.write_text("policy_id,horizon,mean_nondominated,retention_core\nL1_A1,20,False,0.75\n",
                     encoding="utf-8")
    return tmp_path


def test_typed_evidence_keeps_identity_and_missingness(tmp_path):
    path = tmp_path / "example.csv"
    path.write_text("unit_id,config_id,seed,cash_yuan,event_fail,median_days,investment_plan\n"
                    "001,012345,1101,1234.50,True,,0+1\n", encoding="utf-8")
    assert read_typed_csv(path) == [{
        "unit_id": "001", "config_id": "012345", "seed": 1101,
        "cash_yuan": 1234.5, "event_fail": True, "median_days": None,
        "investment_plan": "0+1",
    }]


def test_publication_loads_from_exports_without_databases(tmp_path):
    out = _publication(tmp_path)
    data = load_publication_data(out)
    assert not list(out.rglob("*.sqlite"))
    assert data["manifest"]["base_config_id"] == "012345"
    assert data["tables"]["n1000"]["grid_summary"][0]["mean_nondominated"] is False
    assert data["combat_pairs"]["DPS_01"]["S_A"]["net_gain"] == 0.23


def test_publication_rejects_different_science_snapshot(tmp_path):
    out = _publication(tmp_path)
    snapshot = out / "research/science.json"
    data = json.loads(snapshot.read_text("utf-8"))
    data["config_id"] = "other"
    _json(snapshot, data)
    with pytest.raises(ValueError, match="配置不一致"):
        load_publication_data(out)


def test_publication_rejects_incomplete_primary_study(tmp_path):
    out = _publication(tmp_path)
    _json(out / "research/experiment_manifest.json", {
        "status": "running", "base_config_id": "012345",
    })
    with pytest.raises(ValueError, match="尚未完成"):
        load_publication_data(out)
