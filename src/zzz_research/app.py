"""Local dashboard entry points for standalone HTML and optional Streamlit."""

from __future__ import annotations

import sys
from pathlib import Path

from zzz_research.archetype_presentation import archetype_html
from zzz_research.dashboard_build import TABS, build_dashboard
from zzz_research.report import observations_available, read_csv, read_json


def _streamlit_main(out_dir: str) -> None:  # pragma: no cover - 需要 streamlit
    import streamlit as st

    from zzz_research.combat.proxy import ProxyCombatModel, reference_scenario
    from zzz_research.config import resolve_config
    from zzz_research.contracts import CombatRequest

    out = Path(out_dir)
    st.set_page_config(page_title="研究看板", layout="wide")
    v = read_json(out / "validate.json") or {}
    st.caption(f"config_id {v.get('config_id')} · 行为结果为已完成 run 的模拟；改滑块不会生成新的行为结果")
    t1, t2, t3, t4 = st.tabs([l for _, l in TABS])
    with t1:
        archetypes = archetype_html(out) if observations_available(out) else ""
        if archetypes:
            evolution = out / "report" / "figures" / "archetype_evolution.svg"
            if evolution.is_file():
                st.image(str(evolution))
            st.markdown(archetypes, unsafe_allow_html=True)
        st.dataframe(read_csv(out / "observations" / "repo_file_coverage.csv"))
    with t2:
        cfg = resolve_config(Path("config") / "experiment.json")
        model = ProxyCombatModel(cfg.module("combat"))
        p = model.params
        theta = st.slider("盾覆盖 θ", 0.0, 1.0, 0.4)
        kappa = st.select_slider("对齐 κ", options=list(p.alignment_values), value=p.default_alignment)
        b_ref = st.number_input("B_ref", value=float(cfg.get("scenarios.product_power.B_ref_first")))
        sup = st.selectbox("支援", ["NONE", *sorted(p.support_gains)])
        sc = reference_scenario(p, "dash", theta=theta, uptime=1.0, kappa=kappa)
        model.add_scenario(sc)
        team = ("A", "n1", sup if sup != "NONE" else "n2")
        r = model.evaluate(CombatRequest(team, "A", {"B_ref": b_ref, "pierce_share": 0.0, "cinema": 0,
                                                     "signature_weapon": False}, 1.0, "dash", p.model_version))
        st.metric("代理 CI（参考 HP）", f"{r.ci:.3f}", help="CI≥1 为模型达标代理，非真实满星")
        st.dataframe(read_csv(out / "benchmarks" / "benchmark_pairs.csv")[:200])
    with t3:
        st.dataframe(read_json(out / "reference_runs.json") or [])
    with t4:
        st.dataframe(read_csv(out / "search" / "seed_robustness.csv"))


if __name__ == "__main__":  # pragma: no cover
    args = sys.argv[1:]
    d = args[args.index("--dir") + 1] if "--dir" in args else "outputs/latest"
    try:
        import streamlit  # noqa: F401
        _streamlit_main(d)
    except ImportError:
        print(build_dashboard(d)["path"])
