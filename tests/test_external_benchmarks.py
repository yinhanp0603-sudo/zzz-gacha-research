"""外部校验：判定以真实四分位区间为准，快照与模拟设定的保底参数逐抽对齐。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_external_benchmarks import RNGMOE, TRACKER, compare, rngmoe_tables, characters  # noqa: E402


def test_outlier_does_not_widen_the_consistency_band():
    real = [.17, .24, .26, .72]
    assert compare(real, [.49, .67, .95]) == "模拟偏高"
    assert compare(real, [.20, .25]) == "一致"
    assert compare([.40, .48, .75], [.15, .24, .49]) == "模拟偏低"


def test_scalar_metrics_use_relative_tolerance():
    assert compare(.0162, .0162) == "一致"
    assert compare(.0069, .006) == "一致"
    assert compare(.29, .42) == "模拟偏高"


@pytest.mark.skipif(not RNGMOE.is_file() or not TRACKER.is_file(), reason="external snapshots not present")
def test_snapshot_curve_uses_registered_soft_pity_and_maps_every_featured_agent():
    _, by_game_id = characters()
    banners, curve, _ = rngmoe_tables(by_game_id)
    assert len(banners) == 26 and all(b["featured_role"] for b in banners)
    character = {c["roll"]: c for c in curve if c["pool"] == "character"}
    assert character[73]["simulated_chance"] == pytest.approx(.006)
    assert character[74]["simulated_chance"] == pytest.approx(.066)
    assert character[90]["simulated_chance"] == pytest.approx(min(1, .006 + .06 * 17))
    tracker = json.loads(TRACKER.read_text("utf-8"))
    assert len(tracker["rows"]) == 40
