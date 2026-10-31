"""同种子、同扰动相对基准差值的描述性汇总；不计算显著性或置信区间。

输入为 agg_search_results 的唯一重复行，也支持 CSV 字符串值。
两个目标分别按非缺失差值计数，不以候选各自的种子 SD 代替配对差 SD。
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Optional, Sequence

GROUP_FIELDS = ("experiment_id", "candidate_id", "perturbation_id", "split")
TARGET_FIELDS = {
    "delta_revenue": "delta_revenue_vs_base",
    "delta_retention": "delta_retention_vs_base",
}


def _number(value: Any, field: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value.lower() in ("", "null", "none", "nan"):
            return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 不能是布尔值")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是数值或缺失值，得到 {value!r}") from exc
    if math.isnan(number):
        return None
    if not math.isfinite(number):
        raise ValueError(f"{field} 必须是有限数值，得到 {value!r}")
    return number


def paired_summary(rows: Sequence[Mapping]) -> list[dict]:
    """按实验/候选/扰动/split汇总既有的同seed差值。

    每个输入行代表一个唯一的重复。n=0时全部统计量为NULL；n=1时SD、SE
    为NULL；n>=2时SD为样本标准差，SE=SD/sqrt(n)。金额单位仍是分。
    """
    groups: dict[tuple[str, ...], list[Mapping]] = {}
    for row in rows:
        key = []
        for field in GROUP_FIELDS:
            value = row.get(field)
            if value is None or not str(value).strip():
                raise ValueError(f"缺少配对分组字段 {field}")
            key.append(str(value))
        groups.setdefault(tuple(key), []).append(row)

    out = []
    for key, group in sorted(groups.items()):
        result = {**dict(zip(GROUP_FIELDS, key)), "n_rows": len(group)}
        for prefix, field in TARGET_FIELDS.items():
            values = [number for row in group
                      if (number := _number(row.get(field), field)) is not None]
            n = len(values)
            sd = statistics.stdev(values) if n >= 2 else None
            result.update({
                f"{prefix}_n": n,
                f"{prefix}_mean": statistics.fmean(values) if n else None,
                f"{prefix}_sd": sd,
                f"{prefix}_se": sd / math.sqrt(n) if sd is not None else None,
                f"{prefix}_min": min(values) if n else None,
                f"{prefix}_max": max(values) if n else None,
            })
        out.append(result)
    return out
