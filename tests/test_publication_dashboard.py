"""Publication views keep current evidence separate from historical baselines."""

import json
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from zzz_research.publication_dashboard import (
    PUBLICATION_SCRIPT, build_publication_dashboard, effect_text, lifetime_summary, period_means,
)


def test_km_median_handles_censoring_instead_of_filling_zero():
    base = {"investment_plan": "0+1", "gear_policy": "growth_from_debut", "support_policy": "none"}
    rows = [{**base, "first_failure_or_censor_days": 42, "first_failure_event": False},
            {**base, "first_failure_or_censor_days": 84, "first_failure_event": True},
            {**base, "first_failure_or_censor_days": 126, "first_failure_event": False}]
    assert lifetime_summary(rows)[0]["median_days"] == 84
    assert lifetime_summary([{**r, "first_failure_event": False} for r in rows])[0]["median_days"] == "观察期内未达中位数"


def test_period_means_preserve_windows_and_separate_candidates():
    rows = [{"horizon": 20, "split": "validation", "policy_id": "L1_A1", "t_idx": 1,
             "retention_core": .6, "cash_yuan": 100},
            {"horizon": 20, "split": "validation", "policy_id": "L1_A1", "t_idx": 1,
             "retention_core": .8, "cash_yuan": 300},
            {"horizon": 10, "split": "validation", "policy_id": "L1_A1", "t_idx": 1,
             "retention_core": .9, "cash_yuan": 50}]
    means = period_means(rows)
    assert len(means) == 2
    assert means[0]["cash_yuan"] == 50
    assert means[1]["cash_yuan"] == 200
    assert means[1]["retention_core"] == pytest.approx(.7)


def test_twenty_period_export_supplies_ten_period_prefix():
    rows = [{"horizon": 20, "split": "validation", "policy_id": "L1_A1", "t_idx": t,
             "cash_yuan": t * 100} for t in range(1, 21)]
    means = period_means(rows)
    prefix = [r for r in means if r["horizon"] == 10]
    assert len(prefix) == 10
    assert prefix[-1]["cash_yuan"] == 1000
    assert len([r for r in means if r["horizon"] == 20]) == 20


def test_retention_endpoint_is_already_percentage_points():
    row = {"metric": "retention_core", "unit": "百分点", "mean_diff": -21.302681,
           "ci95_low": -21.683377, "ci95_high": -20.921986}
    assert effect_text(row) == "-21.30pp [-21.68, -20.92]pp"
    assert effect_text({"metric": "cash_yuan", "mean_relative_pct": -.224,
                        "relative_ci95_low": -2.871, "relative_ci95_high": 2.423}) == "-0.22% [-2.87, +2.42]%"


def _fixture():
    grid = [{"horizon": h, "split": split, "policy_id": f"L{lam:g}_A{a:g}",
             "mechanism_strength": lam, "support_gain_scale": a, "n_seeds": 3 if split == "validation" else 5,
             "cash_yuan": 1000 + h + a, "subscription_yuan": 500, "retention_core": .7,
             "retention_all": .6, "mean_nondominated": False, "inclusion_frequency": 0}
            for h in (10, 20) for split in ("train", "validation")
            for lam in (0, .5, 1, 1.5, 2) for a in (.8, 1, 1.2, 1.4, 1.6)]
    periods = [{"horizon": r["horizon"], "split": r["split"], "policy_id": r["policy_id"],
                "t_idx": 1, "retention_core": .7, "retention_all": .6, "cash_yuan": r["cash_yuan"],
                "cumulative_cash_yuan": r["cash_yuan"]} for r in grid]
    return {"manifest": {"base_config_id": "current-science"}, "tables": {"n1000": {"grid_summary": grid, "period_outcomes": periods}, "fixed": {}}, "combat_pairs": {}}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.candidate_rows = 0
        self.in_detail = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if "data-table" in (attrs.get("class") or "").split():
            self.in_detail = attrs.get("id") == "v2-candidate-detail"
        if tag == "tr" and self.in_detail and "data-search" in attrs:
            self.candidate_rows += 1


def test_current_publication_keeps_all_rows_and_uses_current_science(tmp_path):
    build_publication_dashboard(tmp_path, _fixture())
    document = (tmp_path / "dashboard/index.html").read_text("utf-8")
    page = _Page()
    page.feed(document)
    assert all(name in page.ids for name in ("p-data", "p-combat", "p-behavior", "p-search", "publication-data"))
    assert len(page.ids) == len(set(page.ids))
    assert page.candidate_rows == 100
    assert "current-science" in document
    assert "150组" not in document
    assert "84.9%" not in document
    assert "无第三方" not in document


def test_current_selector_javascript_switches_horizon_and_candidate(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is optional for the offline selector smoke test")
    fixture = _fixture()["tables"]["n1000"]
    payload = {"grid": fixture["grid_summary"], "periods": period_means(fixture["period_outcomes"]),
               "design": {"players": 1000, "train_seeds": 5, "validation_seeds": 3}}
    harness = """
const elements={};
function element(id){return elements[id]??=( {value:'',innerHTML:'',textContent:'',addEventListener(){}});}
element('publication-data').textContent=PAYLOAD;
element('behavior-horizon').value='20';element('behavior-split').value='validation';element('behavior-lambda').value='1';element('behavior-a').value='1';
const buttons=[10,20].map(h=>({dataset:{matrixHorizon:String(h)},setAttribute(k,v){this[k]=v},addEventListener(){}}));
const document={getElementById:element,querySelectorAll(){return buttons;}};
SCRIPT
if((element('strategy-matrix').innerHTML.match(/data-candidate=/g)||[]).length!==25)throw Error('matrix incomplete');
if(!element('behavior-caption').textContent.includes('20期'))throw Error('initial horizon wrong');
element('behavior-horizon').value='10';element('behavior-lambda').value='2';element('behavior-a').value='1.6';renderBehavior();renderMatrix(10);
if(!element('behavior-caption').textContent.includes('λ=2，a=1.6 · 10期'))throw Error('selection did not update');
if(!element('behavior-retention').innerHTML.includes('<svg'))throw Error('chart missing');
if(buttons[0]['aria-pressed']!=='true')throw Error('matrix horizon not selected');
""".replace("PAYLOAD", json.dumps(json.dumps(payload, ensure_ascii=False))).replace("SCRIPT", PUBLICATION_SCRIPT)
    script = tmp_path / "selectors.js"
    script.write_text(harness, "utf-8")
    result = subprocess.run([node, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
