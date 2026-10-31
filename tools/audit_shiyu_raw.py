"""独立逐文件核对：CSV 原始行数、字段、成绩缺失与下载清单（不输出 UID）。"""
import csv
import json
from pathlib import Path


root = Path(__file__).resolve().parents[1]
snapshot = root / "data/raw/shiyu_hf"
manifest = json.loads((snapshot / "download_manifest.json").read_text("utf-8"))
rows = []
for item in manifest["files"]:
    path = snapshot / item["file"]
    if path.suffix != ".csv":
        continue
    # 上游存在 cp1252；先验证编码，防止中途回退时重复计数。
    encoding = "utf-8-sig"
    try:
        with path.open(encoding=encoding) as handle:
            while handle.read(1024 * 1024):
                pass
    except UnicodeDecodeError:
        encoding = "cp1252"
    n = missing_uid = missing_result = 0
    is_char = path.stem.endswith("_char")
    with path.open(newline="", encoding=encoding) as handle:
        reader = csv.DictReader(handle)
        for record in reader:
            n += 1
            missing_uid += not bool((record.get("uid") or "").strip())
            if not is_char:
                missing_result += record.get("score") in (None, "") and record.get("time") in (None, "")
        columns = reader.fieldnames
    rows.append({"file": path.name, "kind": "char" if is_char else "battle", "records": n,
                 "missing_uid": missing_uid, "missing_result": missing_result if not is_char else None,
                 "columns": columns, "encoding": encoding, "sha256": item["sha256"]})
result = {"repo_id": manifest["repo_id"], "revision": manifest["revision"], "files": rows,
          "battle_records": sum(r["records"] for r in rows if r["kind"] == "battle"),
          "char_records": sum(r["records"] for r in rows if r["kind"] == "char"),
          "battle_missing_result": sum(r["missing_result"] for r in rows if r["kind"] == "battle")}
target = root / "outputs/benchmark_n200/verification/shiyu_raw_audit.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in result.items() if k != "files"}, ensure_ascii=False))
