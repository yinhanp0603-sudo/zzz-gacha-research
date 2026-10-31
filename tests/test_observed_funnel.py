"""Published sample pools keep mode, roster validity and known results separate."""

import csv
import json

from zzz_research.observed_funnel import funnel_html, funnel_markdown, load_sample_funnel


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _exports(tmp_path):
    _json(tmp_path / "observations/observations_manifest.json", {
        "observed_data_available": True, "n_battles": 150, "n_eligible": 100,
        "quality_issues": ["eligible_without_result:10"],
    })
    _json(tmp_path / "observations/archetype_evolution_manifest.json", {"valid_3man_records": 95})
    _json(tmp_path / "boss_mechanism_stats_manifest.json", {"stage_scope_totals": {
        "normal": {"valid_3man_records": 40, "eligible_records": 43, "known_score_records": 43},
        "advanced": {"valid_3man_records": 10, "eligible_records": 11, "known_score_records": 11},
    }})
    path = tmp_path / "empirical_knob1_boss_lock.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["scope", "stage_scope", "n_clears"])
        writer.writeheader()
        writer.writerows([
            {"scope": "mechanism", "stage_scope": "normal", "n_clears": 20},
            {"scope": "mechanism", "stage_scope": "normal", "n_clears": 15},
            {"scope": "mechanism", "stage_scope": "advanced", "n_clears": 10},
            {"scope": "season", "stage_scope": "normal", "n_clears": 35},
            {"scope": "matched_main_investment", "stage_scope": "normal", "n_clears": 8},
        ])
    return tmp_path


def test_mechanism_pool_restricts_mode_before_excluding_unresolved(tmp_path):
    out = _exports(tmp_path)
    data = load_sample_funnel(out)
    assert data["valid_3man_records"] == 95
    assert data["deadly_assault_valid_records"] == 50
    assert data["unresolved_valid_records"] == 5
    assert data["mechanism_records"] == 45
    assert data["mechanism_records"] != data["valid_3man_records"] - 5
    assert (data["mechanism_normal_records"], data["mechanism_advanced_records"]) == (35, 10)


def test_missing_results_remain_in_eligible_and_roster_pools(tmp_path):
    out = _exports(tmp_path)
    data = load_sample_funnel(out)
    assert data["known_result_records"] == 90
    assert data["eligible_records"] == 100
    assert data["valid_3man_records"] == 95
    assert data["invalid_or_partial_records"] == 5


def test_report_and_dashboard_explain_same_four_pools(tmp_path):
    out = _exports(tmp_path)
    markdown, document = funnel_markdown(out), funnel_html(out)
    assert "| ④ 精确首领机制比较池（危局） | 45 |" in markdown
    assert '<article class="card section-gap" id="observed-sample-funnel">' in document
    assert document.count('<th scope="row">') == 4
    for text in (markdown, document):
        assert "不从出场和配队计数中统一扣除" in text
        assert "普通路 35 条、进阶路 10 条分开统计" in text
        assert "仅使用各模式、关卡及匹配格内已知且有效的成绩" in text


def test_no_imported_data_does_not_render_a_funnel(tmp_path):
    _json(tmp_path / "observations/observations_manifest.json", {"observed_data_available": False})
    assert load_sample_funnel(tmp_path) is None
    assert funnel_html(tmp_path) == funnel_markdown(tmp_path) == ""
