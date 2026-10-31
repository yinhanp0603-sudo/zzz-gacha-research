"""Build descriptive observed-data anchors without changing or running simulation models."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from empirical_common import ROOT, normalized_discs, number, read_csv, write_csv
from empirical_lock import LockAnalysis, lambda_reference, prepare_dimension
from empirical_support import SupportAnalyzer

QUERY = """
SELECT b.battle_id,b.local_uid,b.stage_id,b.scoring_regime,b.score_value,b.clear_time_seconds,
       b.source_row_locator,
       m1.unit_id,m1.known_cinema,m1.known_weapon,
       m2.unit_id,m2.known_cinema,m2.known_weapon,
       m3.unit_id,m3.known_cinema,m3.known_weapon
FROM obs_battle b
LEFT JOIN obs_team_member m1 ON m1.battle_id=b.battle_id AND m1.team_slot=1
LEFT JOIN obs_team_member m2 ON m2.battle_id=b.battle_id AND m2.team_slot=2
LEFT JOIN obs_team_member m3 ON m3.battle_id=b.battle_id AND m3.team_slot=3
WHERE b.snapshot_id=? AND b.combat_season=? AND b.mode=? AND b.is_eligible=1
"""


def profile_map(path):
    result = {}
    if not path.is_file():
        return result
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            key = ((row.get("uid") or "").strip(), (row.get("name") or "").strip())
            if not all(key) or key in result:
                continue
            level = number(row.get("level"))
            result[key] = (int(level) if level is not None and level.is_integer() else None,
                           normalized_discs(row.get("artifacts")), number(row.get("potential")))
    return result


def stamp(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def build(out, raw_dir):
    started = time.perf_counter()
    observations = out / "observations"
    protected = {p:stamp(p) for p in observations.iterdir() if p.is_file()}
    preserved = {p:p.read_bytes() for p in [out/"REPORT.md",out/"report/REPORT.md",out/"dashboard/index.html"] if p.is_file()}
    dimensions = prepare_dimension(out)
    support, lock = SupportAnalyzer(out), LockAnalysis(out, dimensions)
    valid_comps = {tuple(sorted(json.loads(r["comp_units"]))) for r in read_csv(out/"observed_comp_archetype.csv") if r["is_valid_3man"]=="1"}
    issues = {(r["snapshot_id"],r["source_row_locator"]) for r in read_csv(out/"boss_association_issues.csv")}
    scopes=defaultdict(int)
    for row in read_csv(observations/"obs_coverage.csv"):
        scopes[row["source_snapshot_id"],row["combat_season"],row["mode"]]+=int(row["eligible_records"])
    connection=sqlite3.connect((observations/"observations.sqlite").resolve().as_uri()+"?mode=ro",uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA cache_size=-65536")
    connection.execute("BEGIN")
    counts=Counter(); last_snapshot=None; profiles={}
    try:
        for (snapshot,season,mode),expected in sorted(scopes.items()):
            if not expected:
                continue
            if snapshot!=last_snapshot:
                collection=snapshot.rsplit(":",1)[-1]
                profiles=profile_map(raw_dir/f"{collection}_char.csv")
                last_snapshot=snapshot
            seen=0
            for values in connection.execute(QUERY,(snapshot,season,mode)):
                battle,uid,stage,regime,score,seconds,locator,*team=values
                members=[]
                for index in range(0,9,3):
                    unit,cinema,weapon=team[index:index+3]
                    if unit is None or not unit.strip():
                        continue
                    level,discs,potential=profiles.get((uid,unit),(None,None,None))
                    weapon=weapon if weapon and weapon.strip().casefold() not in {"none","nan","null","unknown","n/a"} else None
                    members.append({"unit_id":unit,"known_cinema":cinema,"known_weapon":weapon,
                                    "level":level,"artifacts":discs,"potential":potential})
                valid=tuple(sorted(m["unit_id"] for m in members)) in valid_comps
                record={"battle_id":battle,"local_uid":uid,"snapshot_id":snapshot,"combat_season":season,
                        "mode":mode,"stage_id":stage,"scoring_regime":regime,
                        "score_value":number(score),"clear_time_seconds":number(seconds),
                        "source_row_locator":locator,"members":members,"valid_3man":valid,
                        "boss_association_valid":(snapshot,locator) not in issues}
                lock.consume(record)
                support.process(record)
                seen+=1;counts["eligible_records"]+=1;counts["valid_3man_records"]+=valid
                counts[mode+"_eligible"]+=1
            if seen!=expected:
                raise ValueError(f"Coverage mismatch for {snapshot}/{mode}: {seen} != {expected}")
            print(f"{snapshot.rsplit(':',1)[-1]} {mode}: {counts['eligible_records']:,}/{sum(scopes.values()):,}",flush=True)
    finally:
        connection.close()
    if counts["valid_3man_records"]!=sum(int(r["eligible_records"])for r in read_csv(out/"observed_comp_archetype.csv") if r["is_valid_3man"]=="1"):
        raise ValueError("Full-roster records do not reconcile with the classified composition table")
    lock_rows,lock_summary=lock.finish()
    support_rows,support_summary=support.finish()
    write_csv(out/"empirical_knob1_boss_lock.csv",lock_rows)
    write_csv(out/"empirical_knob2_support_lift.csv",support_rows)
    write_csv(out/"empirical_knob1_lambda_reference.csv",lambda_reference(out))
    for path,before in protected.items():
        if stamp(path)!=before:
            raise ValueError(f"Observation source changed: {path}")
    if any(path.read_bytes()!=before for path,before in preserved.items()):
        raise ValueError("A report or dashboard changed during the table-only analysis")
    import sys
    sys.path.insert(0,str(ROOT/"src"))
    from zzz_research.config import resolve_config
    from zzz_research.pipeline import project_path
    config_id=resolve_config(ROOT/"config/experiment.json").config_id
    expected=json.loads((out/"validate.json").read_text("utf-8"))["config_id"]
    if config_id!=expected:
        raise ValueError("The active scientific configuration differs from the published experiment")
    summary={"status":"completed","config_id":config_id,"source_database":project_path(observations/"observations.sqlite"),
             "counts":dict(counts),"lock":lock_summary,"support":support_summary,
             "observation_sources_unchanged":True,"reports_and_dashboard_unchanged":True,
             "L2_L3_simulations_executed":False,"wall_seconds":round(time.perf_counter()-started,2),
             "identification_status":"conditional associations and reference sensitivity, not causal identification of lambda or a"}
    (out/"empirical_calibration_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    write_notes(out,summary,lock_rows)
    print(json.dumps({"counts":dict(counts),"lock":lock_summary,
                      "support_matched_strata":support_summary["matched_strata"],
                      "support_stable_strata":support_summary["stable_strata"],
                      "wall_seconds":summary["wall_seconds"]},ensure_ascii=False),flush=True)
    return summary


def write_notes(out,summary,lock_rows):
    text=("# 两个旋钮的实证锚点\n\n"
          "统计对象为已提交且合格的完整三人通关记录。主表按原始战斗粒度统计，同一玩家可有多场战斗；"
          "跨采集快照不合并身份。未通关、未持有和未提交记录不可见。\n\n"
          "## 机制锁\n\n"
          "Boss 维表保留 `mech_regime` 五个历史时期字段，新增 `empirical_mech_regime` 细分机制类别、"
          "`mech_desc` 和 `favored_group`。实证表的 `mech_regime` 使用细分机制类别。"
          "对策组在读取成绩前登记，按主核职业、点名体系组件及三异常条件判定；泛用击破不独立定义主核。"
          "以太帷幕类的主核代理只覆盖当前登记的帷幕主核能力，不等于战斗内完整技能触发检测。\n\n"
          "`HHI` 对互斥的核心机制组合签名求份额平方和，多核队不被强行分给一个主 C。"
          "`HHI_core_signature` 使用更细的角色主核签名。对策覆盖、混合主核比例和 HHI 的定义分别保留。"
          "`off_meta_score_ratio` 是非对策组中位分／对策组中位分；`score_comparison_design` 区分原始组间比较和"
          "同房间、同采集快照、主核影画／专武档位／等级相同的单核子组。后者仍不是同一角色、同一全队配置。\n\n"
          "`empirical_knob1_lambda_reference.csv` 只代数计算冻结参数下的五档 λ 输出保留率，"
          "不运行战斗仿真。0.4 与 1.0 是明确标注的盾覆盖参考情景。真实分数既非直接 DPS 也非清除指数，"
          "缺少输出分布、失败队伍与 Boss 实际暴露时长，不能把分数比反解为唯一 λ。\n\n"
          "## 专属体系组件\n\n"
          "分层固定采集快照、赛季、模式、房间、主 C 影画、实际装备名称、60级、盘套装组合与已记录的潜能。"
          "`fixed_third_matched` 额外固定第三名队友身份，并要求目标与替代位具有相同职业；"
          "`main_build_matched` 比较主 C 配装相同但其他队友可不同的组件组合。对照不含指定组件，并至少有一个预登记旧替代位。"
          "替代位可为 A／常驻 S／免费 S，或早于目标组件上线的支援／副输出。无法据此认定全队同金数。\n\n"
          "同一层内用中位分相除，或用无组件用时／有组件用时，得到 `median_score_ratio`、`speed_ratio`。"
          "每组至少20条记录的分层标为可稳定描述；低样本分层仍完整保留。汇总对层内比值按两组样本数的调和重叠权重求几何均值，"
          "没有把分层中位数平均成总样本中位数。汇总参与次数可重叠，不当作独立玩家人数。"
          "`n_shared_players` 显示是否确有同一玩家两种配队；分层匹配不能冒充随机配对实验。\n\n"
          "专武状态来自实际装备名称，缺失武器不当作0+0；其他已知武器为未装备本命专武，可能仍是其他限定 S 武器。"
          "数据没有盘词条、武器等级／叠影、技能等级、完整投入成本与操作水平，装备快照也未必等于战斗瞬间配置。"
          "式舆后期为得分制，不将分数倒算通关速度。出现赛季数是样本覆盖，不能称作延寿或玩家存活。\n\n"
          "观测倍率可为参数范围提供条件性成绩锚点，但不等于因果支援倍率。对照组的旧辅助也能提供增益，"
          "不同于模型中的无增益中性位，不能把新旧辅助间的成绩比直接当作新增支援的绝对倍率。模型中总支援倍率为1+a×g，"
          "a并非总输出倍率：g=0.35时，a∈[0.8,1.6]对应1.28–1.56倍；g=0.45时对应1.36–1.72倍。"
          "两张主表不伪造唯一 λ／a 估计，未改变模型参数。\n\n"
          "## 复现\n\n```powershell\n"
          'python tools/build_empirical_calibration.py --dir "outputs/final_release"\n```\n\n'
          "分析只读 `observations/observations.sqlite` 与已有原始配装 CSV，不下载、不重新导入、不运行 L2/L3。"
          "\n")
    (out/"empirical_calibration_notes.md").write_text(text,encoding="utf-8")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir",type=Path,default=ROOT/"outputs/final_release")
    parser.add_argument("--raw-dir",type=Path,default=ROOT/"data/raw/shiyu_hf")
    args=parser.parse_args()
    build(args.dir.resolve(),args.raw_dir.resolve())
