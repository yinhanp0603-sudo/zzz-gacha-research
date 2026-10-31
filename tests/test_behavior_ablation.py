"""行为结构消融：追新效用只作用于新主C池，缺省与现行模型一致；报告判定保留区间跨零。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from zzz_research.agents import decision as DEC
from zzz_research.config import ConfigError, resolve_config
from zzz_research.publication_report import ablation_verdict_rows, event_window_rows


ROOT = Path(__file__).resolve().parents[1]


def _forecast(ci0=1.05):
    return SimpleNamespace(budget_pulls=80, ci_k=(ci0, ci0 + .02), k_probability=(.4, .6),
                           expected_spent=40.0, expected_topup=10.0, paid_pulls=20)


def test_novelty_term_is_absent_from_current_config_and_added_only_to_new_main_pools():
    base = DEC.DecisionParams.from_config(resolve_config(ROOT / "config/research_v2/experiment.json").module("behavior"))
    m2 = DEC.DecisionParams.from_config(resolve_config(ROOT / "config/research_v2/experiment_m2_novelty.json").module("behavior"))
    assert base.beta_novelty == 0
    assert m2.beta_novelty == 1.5
    plain, gain = DEC.utility(m2, "Budget_40", .4, _forecast())
    novel, novel_gain = DEC.utility(m2, "Budget_40", .4, _forecast(), novelty=True)
    assert novel - plain == pytest.approx(1.5)
    assert gain == novel_gain
    assert DEC.utility(base, "Budget_40", .4, _forecast(), novelty=True) == DEC.utility(base, "Budget_40", .4, _forecast())


def test_m2_entry_differs_from_current_science_only_in_behavior():
    base = resolve_config(ROOT / "config/research_v2/experiment.json")
    m2 = resolve_config(ROOT / "config/research_v2/experiment_m2_novelty.json")
    changed = {name for name in base.input_digests if base.input_digests[name] != m2.input_digests[name]}
    assert changed == {"behavior"}


def test_novelty_block_rejects_negative_or_extra_fields(tmp_path):
    path = tmp_path / "research_v2"
    shutil.copytree(ROOT / "config/research_v2", path)
    target = path / "modules/behavior_m2_novelty.json"
    data = json.loads(target.read_text("utf-8"))
    data["research_v2"]["novelty"] = {"beta_novelty": -1}
    target.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    with pytest.raises(ConfigError, match="novelty"):
        resolve_config(path / "experiment_m2_novelty.json")
    data["research_v2"]["novelty"] = {"beta_novelty": 1, "scale": 2}
    target.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    with pytest.raises(ConfigError, match="novelty"):
        resolve_config(path / "experiment_m2_novelty.json")


def _proposition(model, proposition, horizon, retention, low, high):
    return {"model": model, "proposition": proposition, "horizon": horizon,
            "retention_core_diff_pp": retention, "retention_core_ci95_low_pp": low, "retention_core_ci95_high_pp": high,
            "cash_relative_pct": -1.0, "cash_relative_ci95_low_pct": -2.0, "cash_relative_ci95_high_pct": -.1,
            "new_main_topup_contribution_pp": -2.0, "new_main_topup_contribution_ci95_low_pp": -4.0,
            "new_main_topup_contribution_ci95_high_pp": .2, "support_topup_contribution_pp": .1,
            "subscription_contribution_pp": 1.0}


def test_verdict_keeps_point_direction_and_flags_intervals_crossing_zero():
    rows = [_proposition(model, proposition, 20, -8.5 if model == "M2_NoveltyDriven" else -18.0,
                         -17.0 if model == "M2_NoveltyDriven" else -22.0, .2 if model == "M2_NoveltyDriven" else -13.0)
            for model in ("M0_Baseline", "M1_NoStopSpend", "M2_NoveltyDriven") for proposition in ("A", "B/C")]
    verdict = ablation_verdict_rows({"tables": {"ablation": {"proposition_summary": rows}}})
    retention = verdict[0]
    assert "区间跨零" not in retention[1]
    assert retention[3].startswith("下降 -8.50pp") and retention[3].endswith("区间跨零")
    replacement = verdict[3]
    assert all(cell.startswith("减少") and cell.endswith("区间跨零") for cell in replacement[1:])


def test_event_window_marks_co_launch_without_pre_window():
    rows = [{"scope": "appearance_window", "event_id": "M+Z", "mode": "deadly_assault", "event_season": "2.5.1",
             "event_season_start": "2026-01-02", "main_zh": "叶瞬光", "component_zh": "照", "main_share_change_pp": None},
            {"scope": "score_window_summary", "event_id": "M+Z", "comparison_design": "fixed_third_matched", "phase": "post",
             "n_with_support": 30, "n_legacy_support": 25, "with_rel_p25": .8, "with_rel_p50": 1.0, "with_rel_p75": 1.2,
             "legacy_rel_p25": .7, "legacy_rel_p50": .8, "legacy_rel_p75": .9,
             "matched_median_ratio_geomean": 1.25, "n_stable_strata": 1}]
    (row,) = event_window_rows({"evidence": {"support_event_window": rows}})
    assert row[2] == "同期首发"
    assert row[4] == "1.00 [0.80–1.20]" and row[6] == "1.25（1 层）" and row[7] == "—"
