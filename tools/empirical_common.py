"""Shared definitions for descriptive empirical anchors from observed clears."""

from __future__ import annotations

import csv
import json
import math
import statistics
from array import array
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def median(values):
    return statistics.median(values) if values else None


def ratio(a, b):
    return a / b if a is not None and b is not None and b > 0 else None


def hhi(counts):
    n = sum(counts.values())
    return sum((value / n) ** 2 for value in counts.values()) if n else None


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def track(record):
    return "advanced" if record["mode"] == "deadly_assault" and record["stage_id"] == "2-1" else "normal"


def normalized_discs(value):
    return ", ".join(sorted(part.strip() for part in value.split(",") if part.strip())) if value else None


def signature_state(member, signatures):
    weapon = member.get("known_weapon")
    signature = signatures.get(member["unit_id"], {}).get("weapon")
    return int(weapon == signature) if signature and weapon else None


class Values:
    def __init__(self):
        self.values = array("d")
        self.players = set()

    def add(self, value, uid):
        if value is not None and value > 0:
            self.values.append(value)
            self.players.add(uid)

    def row(self):
        ordered = sorted(self.values)
        return {"n": len(self.values), "n_players": len(self.players),
                "p25": quantile(ordered, .25), "median": median(self.values),
                "p75": quantile(ordered, .75),
                "mean": sum(self.values) / len(self.values) if self.values else None}


def quantile(ordered, q):
    """Linear-interpolated quantile of an already sorted sequence."""
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def weighted_geomean(pairs):
    pairs = [(value, weight) for value, weight in pairs if value is not None and value > 0 and weight > 0]
    return math.exp(sum(math.log(value) * weight for value, weight in pairs) / sum(weight for _, weight in pairs)) if pairs else None


def signed_difference(a, b):
    return a - b if a is not None and b is not None else None
