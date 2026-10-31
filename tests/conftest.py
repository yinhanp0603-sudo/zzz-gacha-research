"""共享测试夹具：按 ShiyuStats 固定修订的原始 CSV 列名构造的合成快照（值为测试数据，不是公开数据）。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

SD_COLS = ["uid", "floor", "node", "rating", "ch1", "ch2", "ch3", "bangboo", "time", "ch1_rank", "ch2_rank", "ch3_rank"]
SD_SCORE_COLS = ["uid", "floor", "node", "rating", "ch1", "ch2", "ch3", "bangboo", "score"]
DA_COLS = ["uid", "floor", "star", "ch1", "ch2", "ch3", "bangboo", "score"]
CHAR_COLS = ["uid", "name", "level", "cons", "weapon", "element", "artifacts"]


def _write(path: Path, cols, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def build_shiyu_snapshot(root: Path) -> dict:
    """两个采集期：
    - 9.0.1（2025-06 采集，SD 计时制，关卡 7-1/7-2）
    - 9.1.1（2026-08 采集，SD 计分制 5-x；DA 含 2-1）
    """
    root.mkdir(parents=True, exist_ok=True)
    vm = {
        "9.0.1": {"collect_date": "15/06/2025", "sd": {"ver": "9.0.1", "start": "01/06/2025", "end": "16/06/2025"},
                  "da": None},
        "9.1.1": {"collect_date": "10/08/2026", "sd": {"ver": "9.1.0", "start": "01/08/2026", "end": "16/08/2026"},
                  "da": {"ver": "9.1.1", "start": "01/08/2026", "end": "15/08/2026"}},
    }
    (root / "versions_config.json").write_text(json.dumps(vm), encoding="utf-8")
    snap = root / "snapshot"
    snap.mkdir()
    sd_old = [
        # u1：两关合格（S），rank 列给出影画
        {"uid": "u1", "floor": 7, "node": 1, "rating": "S", "ch1": "Ellen", "ch2": "Lycaon", "ch3": "Soukaku",
         "time": 100, "ch1_rank": 0, "ch2_rank": 0, "ch3_rank": 6},
        {"uid": "u1", "floor": 7, "node": 2, "rating": "S", "ch1": "Miyabi", "ch2": "Yanagi", "ch3": "Lycaon",
         "time": 140, "ch1_rank": 1, "ch2_rank": 0, "ch3_rank": 0},
        # u2：A 评级 → 不合格
        {"uid": "u2", "floor": 7, "node": 1, "rating": "A", "ch1": "Ellen", "ch2": "Lycaon", "ch3": "Soukaku", "time": 300},
        # u3：关卡 6-1 不在计时制关卡集合
        {"uid": "u3", "floor": 6, "node": 1, "rating": "S", "ch1": "Ellen", "ch2": "Lycaon", "ch3": "Soukaku", "time": 90},
        {"uid": "u3", "floor": 7, "node": 1, "rating": "S", "ch1": "Miyabi", "ch2": "Lycaon", "ch3": "Soukaku", "time": 120},
        # u1 在别的 uid 之后再次出现 → 整块按源码规则排除
        {"uid": "u1", "floor": 7, "node": 1, "rating": "S", "ch1": "Ellen", "ch2": "Lycaon", "ch3": "Soukaku", "time": 80},
        # u4：空队伍
        {"uid": "u4", "floor": 7, "node": 1, "rating": "S", "time": 100},
        # u5：无 rank 列 → 影画取自 _char 资料
        {"uid": "u5", "floor": 7, "node": 2, "rating": "S", "ch1": "Miyabi", "ch2": "Yanagi", "ch3": "Soukaku", "time": 160},
    ]
    _write(snap / "9.0.1.csv", SD_COLS, sd_old)
    _write(snap / "9.0.1_char.csv", CHAR_COLS, [
        {"uid": "u5", "name": "Miyabi", "level": 60, "cons": 2, "weapon": "Hailstorm Shrine", "element": "Ice", "artifacts": "x"},
        {"uid": "u1", "name": "Ellen", "level": 60, "cons": 0, "weapon": "Deep Sea Visitor", "element": "Ice", "artifacts": "x"},
    ])
    sd_new = [
        {"uid": "v1", "floor": 5, "node": 1, "rating": "S", "ch1": "Miyabi", "ch2": "Yanagi", "ch3": "Lycaon", "score": 42000},
        {"uid": "v2", "floor": 5, "node": 2, "rating": "S", "ch1": "Ellen", "ch2": "Lycaon", "ch3": "Soukaku", "score": 39000},
        {"uid": "v3", "floor": 7, "node": 1, "rating": "S", "ch1": "Ellen", "ch2": "Lycaon", "ch3": "Soukaku", "score": 39000},
    ]
    _write(snap / "9.1.1.csv", SD_SCORE_COLS, sd_new)
    da = [
        {"uid": "d1", "floor": 1, "star": 3, "ch1": "Miyabi", "ch2": "Yanagi", "ch3": "Lycaon", "score": 30000},
        {"uid": "d1", "floor": 4, "star": 1, "ch1": "Ellen", "ch2": "Lycaon", "ch3": "Soukaku", "score": 12000},
        {"uid": "d2", "floor": 2, "star": 2, "ch1": "Ellen", "ch2": "Lycaon", "ch3": "Soukaku", "score": 20000},
    ]
    _write(snap / "9.1.1_da.csv", DA_COLS, da)
    _write(snap / "9.1.1_char.csv", CHAR_COLS, [])
    chars = {n: {"name": n, "availability": a} for n, a in
             (("Ellen", "Limited S"), ("Miyabi", "Limited S"), ("Yanagi", "Limited S"), ("Lycaon", "Standard S"),
              ("Soukaku", "A"))}
    (root / "characters.json").write_text(json.dumps(chars), encoding="utf-8")
    (root / "repo_files.csv").write_text("9.0.1.csv\n9.0.1_char.csv\n9.1.1.csv\n9.1.1_da.csv\n", encoding="utf-8")
    return {"root": root, "snapshot": snap, "version_map": root / "versions_config.json",
            "characters": root / "characters.json", "repo_files": root / "repo_files.csv"}


@pytest.fixture()
def shiyu_snapshot(tmp_path):
    return build_shiyu_snapshot(tmp_path / "shiyu")
