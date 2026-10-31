"""M-OBS 3.2：公开样本描述指标（只用 is_eligible=1 的合格记录）。

- sample_players 与 appearance_player_share 以“快照内唯一 ID”为单位；appearance_per_clear 以队伍记录为单位，
  两者分开命名，不平均或互换。
- 成绩按评分制度分开输出 n / 中位数 / P25 / P75（线性插值，含端点），算术平均仅作辅助。
- 投资关联只在资料已知者内比较，并披露缺失；称“关联比较”，不称购买或因果收益。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from ..observations.quality import SMALL_SAMPLE_PLAYERS


@dataclass(frozen=True)
class Scope:
    snapshot_id: str
    combat_season: str
    mode: str
    stages: tuple[str, ...]

    def where(self, alias: str = "b") -> tuple[str, list]:
        ph = ",".join("?" * len(self.stages))
        return (f"{alias}.is_eligible=1 AND {alias}.local_uid IS NOT NULL AND TRIM({alias}.local_uid)<>'' AND "
                f"{alias}.snapshot_id=? AND {alias}.combat_season=? AND "
                f"{alias}.mode=? AND {alias}.stage_id IN ({ph})",
                [self.snapshot_id, self.combat_season, self.mode, *self.stages])


def quantile(sorted_vals: Sequence[float], q: float) -> Optional[float]:
    """线性插值分位数（与 numpy 默认 'linear' 一致）。空集返回 None。"""
    n = len(sorted_vals)
    if n == 0:
        return None
    pos = (n - 1) * q
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def describe(values: Sequence[float]) -> dict:
    v = sorted(values)
    return {"n": len(v), "median": quantile(v, 0.5), "p25": quantile(v, 0.25), "p75": quantile(v, 0.75),
            "mean": (sum(v) / len(v)) if v else None}


def sample_players(writer: Any, scope: Scope) -> int:
    w, p = scope.where()
    return writer.query(f"SELECT COUNT(DISTINCT b.local_uid) FROM obs_battle b WHERE {w}", p)[0][0]


def appearance(writer: Any, scope: Scope) -> list[dict]:
    """每个角色：出场人数占比（去重）与每条合格队伍出场率。"""
    w, p = scope.where()
    n_players = sample_players(writer, scope)
    n_clears = writer.query(f"SELECT COUNT(*) FROM obs_battle b WHERE {w}", p)[0][0]
    rows = writer.query(f"""
        SELECT m.unit_id, COUNT(DISTINCT b.local_uid), COUNT(DISTINCT b.battle_id)
        FROM obs_battle b JOIN obs_team_member m ON m.battle_id=b.battle_id
        WHERE {w} GROUP BY m.unit_id ORDER BY m.unit_id""", p)
    out = []
    for unit, players, clears in rows:
        out.append({"unit_id": unit, "players": players, "sample_players": n_players,
                    "appearance_player_share": players / n_players if n_players else None,
                    "clears": clears, "eligible_clears": n_clears,
                    "appearance_per_clear": clears / n_clears if n_clears else None,
                    "small_sample": n_players < SMALL_SAMPLE_PLAYERS})
    return sorted(out, key=lambda r: (-(r["appearance_player_share"] or 0), r["unit_id"]))


def results_by_stage(writer: Any, scope: Scope) -> list[dict]:
    w, p = scope.where()
    rows = writer.query(f"""SELECT b.stage_id, b.scoring_regime, b.score_value, b.clear_time_seconds, b.local_uid
                            FROM obs_battle b WHERE {w}""", p)
    groups: dict[tuple[str, str], list] = {}
    for stage, regime, score, secs, uid in rows:
        groups.setdefault((stage, regime), []).append((score if score is not None else secs, uid))
    out = []
    for (stage, regime), vals in sorted(groups.items()):
        d = describe([v for v, _ in vals if v is not None])
        players = len({u for _, u in vals})
        out.append({"stage_id": stage, "scoring_regime": regime, **d, "players": players,
                    "small_sample": players < SMALL_SAMPLE_PLAYERS})
    return out


def team_compositions(writer: Any, scope: Scope, top: Optional[int] = None) -> list[dict]:
    w, p = scope.where()
    rows = writer.query(f"""SELECT b.battle_id, m.unit_id FROM obs_battle b JOIN obs_team_member m
                            ON m.battle_id=b.battle_id WHERE {w} ORDER BY b.battle_id, m.unit_id""", p)
    teams: dict[str, list[str]] = {}
    for bid, u in rows:
        teams.setdefault(bid, []).append(u)
    freq: dict[tuple[str, ...], int] = {}
    for units in teams.values():
        key = tuple(sorted(units))
        freq[key] = freq.get(key, 0) + 1
    total = len(teams)
    out = [{"team": " + ".join(k), "records": n, "share": n / total if total else None}
           for k, n in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))]
    return out[:top] if top else out


def investment_association(writer: Any, scope: Scope, characters: Mapping[str, Mapping[str, Any]],
                           unit_id: str) -> dict:
    """限定 S 角色：资料已知时“无已知影画(0)” vs “有已知影画(≥1)”的成绩描述；缺失单列，不并入 0 组。"""
    avail = (characters.get(unit_id) or {}).get("availability")
    if avail != "Limited S":
        return {"unit_id": unit_id, "status": "not_limited_s", "availability": avail}
    w, p = scope.where()
    rows = writer.query(f"""SELECT m.known_cinema, COALESCE(b.score_value, b.clear_time_seconds), b.scoring_regime
                            FROM obs_battle b JOIN obs_team_member m ON m.battle_id=b.battle_id
                            WHERE {w} AND m.unit_id=?""", [*p, unit_id])
    regimes = {r[2] for r in rows}
    if len(regimes) > 1:
        return {"unit_id": unit_id, "status": "mixed_regime"}
    zero = [v for c, v, _ in rows if c == 0 and v is not None]
    plus = [v for c, v, _ in rows if c is not None and c >= 1 and v is not None]
    missing = sum(1 for c, _, _ in rows if c is None)
    return {"unit_id": unit_id, "status": "ok", "scoring_regime": next(iter(regimes), None),
            "cinema_0": describe(zero), "cinema_1plus": describe(plus), "cinema_unknown_records": missing,
            "label": "关联比较（非购买/因果）"}


def scopes(writer: Any) -> list[Scope]:
    """数据中实际存在的 (快照, 赛季, 模式) 及其合格关卡集合。"""
    rows = writer.query("""SELECT snapshot_id, combat_season, mode, GROUP_CONCAT(DISTINCT stage_id)
                           FROM obs_battle WHERE is_eligible=1 AND combat_season IS NOT NULL
                             AND local_uid IS NOT NULL AND TRIM(local_uid)<>''
                           GROUP BY 1,2,3 ORDER BY 1,2,3""")
    return [Scope(s, c, m, tuple(sorted(st.split(",")))) for s, c, m, st in rows]
