"""体系化合成世界的虚拟实验：A 组售后类型（混合型另扫副输出份额 ρ）× 环境 × λ，B 组新体系换代锁 × 旧主流售后。

每格生成一份完整配置（复制 V2 模块，只替换商品目录、机制排期与配对），N=200、复核种子、全日志运行。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzz_research.config import resolve_config
from zzz_research.pipeline import project_path, simulate, write_json

BASE = ROOT / "config/research_v2"
SEEDS = (9101, 9102, 9103)
PLAYERS = 200
STRONG_LIFT = {"defense": .15, "window": .15, "barrier": .15}
# 体系 → 机制亲和度（防御、输出窗口、壁垒）；对口机制 0.75，其余较低
ARCHETYPES = {
    "L": {"label": "大体系（异常型）", "affinities": {"defense": .45, "window": .75, "barrier": .1}},
    "S": {"label": "小体系（命破型）", "affinities": {"defense": .75, "window": .1, "barrier": .45}},
    "N": {"label": "新体系（锋御型）", "affinities": {"defense": .45, "window": .1, "barrier": .75}},
}
# 期 → (商品, 类型, 体系)；P07 与 P15 是实验商品
CATALOG = {
    1: ("L01", "normal", "L"), 2: ("S02", "piercing", "S"), 3: ("L03", "piercing", "L"),
    4: ("SUP_L04", "support", "L"), 5: ("L05", "normal", "L"), 6: ("S06", "normal", "S"),
    7: ("P07", "support", "L"), 8: ("S08", "normal", "S"), 9: ("L09", "normal", "L"),
    10: ("SUP_S10", "support", "S"), 11: ("L11", "piercing", "L"), 12: ("S12", "normal", "S"),
    13: ("L13", "normal", "L"), 14: ("N14", "normal", "N"), 15: ("P15", "support", "L"),
    16: ("N16", "piercing", "N"), 17: ("SUP_N17", "support", "N"), 18: ("L18", "normal", "L"),
    19: ("S19", "piercing", "S"), 20: ("N20", "normal", "N"),
}
GAINS = {"SUP_L04": .15, "SUP_S10": .23, "SUP_N17": .23, "system": .23, "hybrid": .30, "postsale": .30}
# 混合型在辅助位的副输出份额 ρ：0 为纯辅助，其余三档做敏感性扫描
CO_OUTPUT = {"hybrid": 0.0, "hybrid_r25": .25, "hybrid_r50": .50, "hybrid_r75": .75}
L_FIT, L_UNFIT, NEUTRAL, ANTI_L = "window", "barrier", "defense", "barrier"


def cells() -> list[dict]:
    out = []
    for product in ("control", "narrow", "system", *CO_OUTPUT):
        for env in ("fit", "unfit"):
            for lam in (1.0, 2.0):
                out.append({"block": "A", "cell_id": f"A_{product}_{env}_L{lam:g}", "product": product,
                            "env": env, "lam": lam, "lock": "none", "postsale": "no",
                            "co_output": CO_OUTPUT.get(product, 0.0)})
    for lock in ("none", "lock"):
        for postsale in ("no", "yes"):
            out.append({"block": "B", "cell_id": f"B_{lock}_{postsale}", "product": "system", "env": "neutral",
                        "lam": 1.0, "lock": lock, "postsale": postsale, "co_output": 0.0})
    return out


def _members(archetype: str, upto: int = 20) -> list[str]:
    return [u for t, (u, role, a) in CATALOG.items() if a == archetype and role != "support" and t <= upto]


def world(cell: dict) -> tuple[dict, dict]:
    """返回该格的 scenarios 与 combat 模块。"""
    scenarios = json.loads((BASE / "modules/scenarios.json").read_text("utf-8"))
    combat = json.loads((BASE / "modules/combat.json").read_text("utf-8"))
    old = {e["t"]: e for e in scenarios["events"]}
    catalog = dict(CATALOG)
    if cell["product"] in CO_OUTPUT:
        catalog[7] = ("H07", "hybrid", "L")
    events = [{"t": t, "unit_id": unit, "role": role, "theta": old[t]["theta"], "uptime": old[t]["uptime"],
               "banner_id": f"ARC_P{t:02d}", "event_order": 1} for t, (unit, role, _) in sorted(catalog.items())]
    schedule = {t: NEUTRAL for t in range(1, 21)}
    for t in (7, 8, 9):
        schedule[t] = {"fit": L_FIT, "unfit": L_UNFIT, "neutral": NEUTRAL}[cell["env"]]
    if cell["lock"] == "lock":
        for t in (14, 15, 16):
            schedule[t] = ANTI_L
    scenarios.update(events=events, description=f"体系化合成世界：{cell['cell_id']}")
    scenarios["research_v2"]["mechanism_schedule"] = {str(t): v for t, v in schedule.items()}

    anchors = {u: a for t, (u, role, a) in catalog.items() if role != "support"}
    profiles = {"starter_dps": combat["research_v2"]["unit_profiles"]["starter_dps"]}
    profiles.update({u: {"affinities": dict(ARCHETYPES[a]["affinities"])} for u, a in anchors.items()})
    pairs: dict[str, dict] = {u: {} for u in anchors}

    def pair(support: str, mains, gain: float) -> None:
        for main in mains:
            if main != support:
                pairs[main][support] = {"net_gain": gain, "match_lift": dict(STRONG_LIFT)}

    l_mains = [u for u, a in anchors.items() if a == "L"]
    pair("SUP_L04", l_mains, GAINS["SUP_L04"])
    pair("SUP_S10", [u for u, a in anchors.items() if a == "S"], GAINS["SUP_S10"])
    pair("SUP_N17", [u for u, a in anchors.items() if a == "N"], GAINS["SUP_N17"])
    if cell["product"] == "narrow":
        pair("P07", ["L03"], GAINS["system"])
    elif cell["product"] == "system":
        pair("P07", l_mains, GAINS["system"])
    elif cell["product"] in CO_OUTPUT:
        pair("H07", l_mains, GAINS["hybrid"])
    if cell["postsale"] == "yes":
        pair("P15", l_mains, GAINS["postsale"])
    flex = [u for t, (u, role, _) in catalog.items() if role in ("support", "hybrid")]
    combat["support"]["gains"] = {u: .35 for u in flex}
    combat["research_v2"]["unit_profiles"] = profiles
    combat["research_v2"]["support_pairs"] = {m: p for m, p in pairs.items() if p}
    combat["research_v2"]["mechanism_strength"] = cell["lam"]
    if cell["co_output"]:
        combat["research_v2"]["co_output"] = {"H07": cell["co_output"]}
    combat["description"] = "体系化合成世界：配对由体系成员关系生成；数值为情景设定。"
    return scenarios, combat


def write_cell_config(out: Path, cell: dict) -> Path:
    directory = out / "configs" / cell["cell_id"]
    (directory / "modules").mkdir(parents=True, exist_ok=True)
    entry = json.loads((BASE / "experiment.json").read_text("utf-8"))
    entry.update(profile_id=f"archetype_{cell['cell_id']}",
                 description="体系化合成世界的售后类型与换代实验", execution={**entry["execution"], "output_root": str(out)})
    scenarios, combat = world(cell)
    for name, rel in entry["modules"].items():
        source = BASE / rel
        data = {"scenarios": scenarios, "combat": combat}.get(name) or json.loads(source.read_text("utf-8"))
        (directory / rel).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (directory / "experiment.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (directory / "cell.json").write_text(json.dumps(cell, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return directory / "experiment.json"


def run_cell(entry: str, out: str, cell: dict, seed: int) -> dict:
    started = time.perf_counter()
    cfg = resolve_config(entry)
    directory = Path(out) / "runs" / cell["cell_id"]
    db = directory / f"{cfg.module('experiments')['base_scenario']}_seed{seed}.sqlite"
    if not db.exists():
        simulate(entry, directory, seed=seed, n_players=PLAYERS, log_mode="full")
    return {**cell, "seed": seed, "config_id": cfg.config_id, "database": project_path(db),
            "wall_seconds": time.perf_counter() - started}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/research_v2_archetype")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    plan = cells()
    entries = {c["cell_id"]: write_cell_config(out, c) for c in plan}
    for c in plan:
        resolve_config(entries[c["cell_id"]])
    print(f"体系实验：{len(plan)} 格 × {len(SEEDS)} 种子 = {len(plan) * len(SEEDS)} 次运行", flush=True)
    if args.plan_only:
        return 0
    manifest = {"cells": plan, "seeds": list(SEEDS), "players": PLAYERS, "log_mode": "full",
                "archetypes": ARCHETYPES, "catalog": {t: list(v) for t, v in CATALOG.items()}, "gains": GAINS, "co_output": CO_OUTPUT,
                "mechanisms": {"L_fit": L_FIT, "L_unfit": L_UNFIT, "neutral": NEUTRAL, "anti_L_lock": ANTI_L},
                "status": "running", "runs": []}
    write_json(out / "experiment_manifest.json", manifest)
    try:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(run_cell, str(entries[c["cell_id"]]), str(out), c, s) for c in plan for s in SEEDS]
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                manifest["runs"].append(result)
                write_json(out / "experiment_manifest.json", manifest)
                print(f"[{i}/{len(futures)}] {result['cell_id']} seed={result['seed']}", flush=True)
        manifest["runs"].sort(key=lambda r: (r["cell_id"], r["seed"]))
        manifest.update(status="completed", completed_runs=len(manifest["runs"]))
    except BaseException as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        write_json(out / "experiment_manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
