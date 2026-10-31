"""下载公开、固定修订的 ShiyuData；只选择当前适配器登记的战斗与角色 CSV。

先 --inspect-only 记录远端修订和下载计划，再 --download 执行该计划。
不使用登录令牌，不上传，不运行上游交互式上传/清理脚本。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


REPO_ID = "LvlUrArti/ShiyuData"
ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "data/raw/SRC_SHIYU_meta_d930cec50bfd"


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def inspect(out):
    from huggingface_hub import HfApi

    info = HfApi(token=False).dataset_info(REPO_ID, files_metadata=True)
    versions = json.loads((META / "versions_config.json").read_text("utf-8"))
    expected = {f"{v}{suffix}.csv" for v in versions for suffix in ("", "_da", "_char")}
    files = []
    for item in info.siblings:
        if item.rfilename not in expected and item.rfilename != "README.md":
            continue
        lfs = getattr(item, "lfs", None)
        files.append({"file": item.rfilename, "size": item.size,
                      "lfs_sha256": getattr(lfs, "sha256", None) if lfs else None})
    if not any(x["file"].endswith(".csv") for x in files):
        raise RuntimeError("数据集没有与当前版本映射匹配的 CSV，停止下载")
    card = info.card_data.to_dict() if info.card_data is not None else {}
    plan = {"repo_id": REPO_ID, "repo_type": "dataset", "revision": info.sha,
            "dataset_url": f"https://huggingface.co/datasets/{REPO_ID}",
            "inspected_at_utc": now(), "status": "planned", "files": files,
            "total_bytes": sum(x["size"] or 0 for x in files),
            "dataset_card_license": card.get("license"),
            "metadata_revision": "d930cec50bfde460d1a6ebd9acef8b240a676000",
            "selection": "registered collection periods; battle and char CSV; excludes build and tracking CSV",
            "missing_battle_periods": [v for v in versions
                                       if not any(x["file"] in (f"{v}.csv", f"{v}_da.csv") for x in files)]}
    save(out / "download_manifest.json", plan)
    print(json.dumps({k: v for k, v in plan.items() if k != "files"}, ensure_ascii=False, indent=2))
    print(f"Selected files: {len(files)}", flush=True)


def download(out):
    from huggingface_hub import snapshot_download

    manifest = out / "download_manifest.json"
    plan = json.loads(manifest.read_text("utf-8"))
    if plan["repo_id"] != REPO_ID or len(plan["revision"]) != 40:
        raise RuntimeError("下载计划没有固定到预期数据集的提交修订")
    plan.update(status="downloading", download_started_at_utc=now())
    save(manifest, plan)
    snapshot_download(repo_id=REPO_ID, repo_type="dataset", revision=plan["revision"],
                      local_dir=out, allow_patterns=[x["file"] for x in plan["files"]],
                      token=False, max_workers=4)
    for item in plan["files"]:
        path = out / item["file"]
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if item["size"] is not None and path.stat().st_size != item["size"]:
            raise RuntimeError(f"文件大小不符：{path.name}")
        if item["lfs_sha256"] and digest != item["lfs_sha256"]:
            raise RuntimeError(f"文件 SHA-256 不符：{path.name}")
        item["sha256"] = digest
        item["verified_bytes"] = path.stat().st_size
    plan.update(status="completed", download_completed_at_utc=now())
    save(manifest, plan)
    print(json.dumps({"status": "completed", "revision": plan["revision"],
                      "files": len(plan["files"]), "bytes": plan["total_bytes"],
                      "path": str(out)}, ensure_ascii=False), flush=True)


def verify_remote_hashes(out):
    """以下载客户端保存的服务器 ETag 核对所有文件：Git blob SHA-1 或 LFS SHA-256。"""
    manifest = out / "download_manifest.json"
    plan = json.loads(manifest.read_text("utf-8"))
    for item in plan["files"]:
        path = out / item["file"]
        metadata = out / ".cache/huggingface/download" / f"{item['file']}.metadata"
        revision, etag = metadata.read_text("utf-8").splitlines()[:2]
        if revision != plan["revision"] or len(etag) not in (40, 64):
            raise RuntimeError(f"服务器下载元数据异常：{item['file']}")
        digest = hashlib.sha1() if len(etag) == 40 else hashlib.sha256()
        if len(etag) == 40:
            digest.update(f"blob {path.stat().st_size}\0".encode("ascii"))
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != etag:
            raise RuntimeError(f"服务器哈希不一致：{item['file']}")
        item.update(upstream_etag=etag, upstream_hash_verified=True,
                    upstream_hash_algorithm="git_blob_sha1" if len(etag) == 40 else "lfs_sha256")
    plan["all_upstream_hashes_verified_at_utc"] = now()
    save(manifest, plan)
    print(json.dumps({"verified_upstream_hashes": len(plan["files"]), "revision": plan["revision"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect-only", action="store_true")
    mode.add_argument("--download", action="store_true")
    mode.add_argument("--verify", action="store_true", help="离线核对下载客户端记录的服务器内容哈希")
    parser.add_argument("--out", type=Path, default=ROOT / "data/raw/shiyu_hf")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.inspect_only:
        inspect(args.out)
    elif args.download:
        download(args.out)
    else:
        verify_remote_hashes(args.out)
