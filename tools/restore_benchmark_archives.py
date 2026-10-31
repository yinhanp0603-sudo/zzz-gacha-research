"""Restore the supplied benchmark archives without overwriting mismatched files.

ZIP CRC is checked while streaming, and each final file is read back for SHA-256.
No simulation is performed. The source archives and manifest are retained.
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import zipfile


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def restore(root):
    root = root.resolve()
    archives = root / "search" / "runs_zip"
    manifest = archives / "runs_manifest.csv"
    with manifest.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 653 or len({r["file"] for r in rows}) != 653:
        raise ValueError("Expected 653 distinct database files in the transfer manifest")
    groups = {}
    for r in rows:
        name = r["file"]
        if not re.fullmatch(r"[A-Za-z0-9_]+\.sqlite", name):
            raise ValueError(f"Unsafe database filename: {name}")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", r["sha256"]):
            raise ValueError(f"Invalid SHA-256: {name}")
        group = r["archive"]
        if group != "reference" and group not in {str(i) for i in range(1, 29)}:
            raise ValueError(f"Unexpected archive group: {group}")
        groups.setdefault(group, {})[name] = r["sha256"].lower()
    if len(groups.get("reference", {})) != 3 or sum(len(v) for k, v in groups.items() if k != "reference") != 650:
        raise ValueError("Expected 650 search databases and 3 reference databases")
    expanded = 0
    jobs = []
    for group, expected in sorted(groups.items(), key=lambda kv: (kv[0] == "reference", int(kv[0]) if kv[0] != "reference" else 0)):
        archive = archives / ("reference_dbs.zip" if group == "reference" else f"runs_part{int(group):02d}.zip")
        destination = root / ("reference" if group == "reference" else "search/runs")
        with zipfile.ZipFile(archive) as z:
            entries = [i for i in z.infolist() if not i.is_dir()]
            if len(entries) != len(expected) or {i.filename for i in entries} != set(expected):
                raise ValueError(f"Archive contents differ from manifest: {archive.name}")
            for i in entries:
                if stat.S_ISLNK(i.external_attr >> 16) or i.flag_bits & 1:
                    raise ValueError(f"Unsupported archive entry: {i.filename}")
                target = (destination / i.filename).resolve()
                if not target.is_relative_to(destination.resolve()) or not target.is_relative_to(root):
                    raise ValueError("Archive target escapes benchmark directory")
                expanded += i.file_size
            jobs.append((archive, destination, expected))
    if shutil.disk_usage(root).free < expanded + 1024 ** 3:
        raise OSError("Insufficient free space for restoration and aggregate database")
    restored = reused = 0
    checks = []
    started = time.perf_counter()
    for archive, destination, expected in jobs:
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                if entry.is_dir():
                    continue
                target = destination / entry.filename
                if target.exists():
                    if target.stat().st_size != entry.file_size or sha256(target) != expected[entry.filename]:
                        raise ValueError(f"Existing file differs; refusing overwrite: {target}")
                    reused += 1
                else:
                    fd, tmp_name = tempfile.mkstemp(prefix=".restore_", suffix=".tmp", dir=destination)
                    tmp = Path(tmp_name)
                    try:
                        digest = hashlib.sha256()
                        with os.fdopen(fd, "wb") as out, z.open(entry) as source:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                digest.update(chunk)
                                out.write(chunk)
                        if digest.hexdigest() != expected[entry.filename] or tmp.stat().st_size != entry.file_size:
                            raise ValueError(f"Archive data SHA-256 mismatch: {entry.filename}")
                        os.replace(tmp, target)
                    finally:
                        if tmp.exists():
                            if not tmp.resolve().is_relative_to(destination.resolve()):
                                raise ValueError("Temporary path escapes destination")
                            tmp.unlink()
                    restored += 1
                actual = sha256(target)
                if actual != expected[entry.filename]:
                    raise ValueError(f"Extracted file read-back mismatch: {target}")
                checks.append(dict(file=entry.filename, sha256=actual, bytes=entry.file_size,
                                   archive=archive.name, path=str(target.relative_to(root))))
        print(f"{archive.name}: verified {len(expected)}; total {len(checks)}/653", flush=True)
    result = dict(status="passed", manifest_sha256=sha256(manifest), restored=restored, reused=reused,
                  search_databases=650, reference_databases=3, sha256_mismatches=0,
                  expanded_bytes=expanded, elapsed_seconds=time.perf_counter() - started,
                  archives_retained=True, files=checks)
    receipt = root / "verification" / "archive_restore.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "files"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("outputs/benchmark_n200"))
    restore(parser.parse_args().root)
