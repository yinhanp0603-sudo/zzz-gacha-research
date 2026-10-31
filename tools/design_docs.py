"""Maintain modular design documents; does not run research or simulations."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "docs" / "design"
MANIFEST = DESIGN / "module_manifest.json"
PROFILE = ROOT / "config" / "experiment.json"
LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^\n)]+)\)")


def markdown_links(text):
    # Formula brackets such as [D(...)-f](1-p) are not Markdown links.
    protected = []
    for pattern in (r"^```[^\n]*\n.*?^```[^\n]*$", r"\\\[.*?\\\]", r"`[^`\n]+`"):
        protected.extend((m.start(), m.end())
                         for m in re.finditer(pattern, text, flags=re.MULTILINE | re.DOTALL))
    for match in LINK.finditer(text):
        if not any(start <= match.start() < end for start, end in protected):
            yield match


def replace_links(text, transform):
    result, previous = [], 0
    for match in markdown_links(text):
        result.extend((text[previous:match.start()], transform(match)))
        previous = match.end()
    result.append(text[previous:])
    return "".join(result)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    def invalid(value):
        raise ValueError(f"Non-finite JSON value: {value}")
    return json.loads(path.read_text(encoding="utf-8-sig"),
                      object_pairs_hook=unique_object, parse_constant=invalid)


def local(path, base=ROOT):
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError(f"Reference outside project: {path}")
    return resolved


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load():
    manifest = read_json(MANIFEST)
    profile = read_json(PROFILE)
    configs = {name: read_json(local(path, PROFILE.parent))
               for name, path in profile["modules"].items()}
    return manifest, profile, configs


def validate(manifest, profile, configs):
    modules = manifest["modules"]
    ids = [m["id"] for m in modules]
    require(len(set(ids)) == len(ids), "Duplicate module ID")
    by_id = {m["id"]: m for m in modules}
    owners = {}
    for module in modules:
        require(local(module["document"]).is_file(), f"Missing module: {module['id']}")
        for dependency in module["dependencies"] + module.get("consumes_outputs_of", []):
            require(dependency in by_id, f"Unknown dependency: {dependency}")
        for path in module["configuration_files"]:
            require(path not in owners, f"Multiple configuration owners: {path}")
            owners[path] = module["id"]
    visiting, done = set(), set()

    def visit(module_id):
        require(module_id not in visiting, f"Dependency cycle: {module_id}")
        if module_id in done:
            return
        visiting.add(module_id)
        for dependency in by_id[module_id]["dependencies"]:
            visit(dependency)
        visiting.remove(module_id)
        done.add(module_id)
    for module_id in ids:
        visit(module_id)
    paths = [PROFILE] + [local(p, PROFILE.parent) for p in profile["modules"].values()]
    for path in paths:
        require(path.relative_to(ROOT).as_posix() in owners,
                f"Configuration has no owner: {path}")
    population, behavior = configs["population"], configs["behavior"]
    tiers = population["tiers"]
    tier_ids = [t["id"] for t in tiers]
    require(len(set(tier_ids)) == len(tier_ids), "Duplicate player tier")
    require(population["n_players"] > 0 and isinstance(population["n_players"], int),
            "n_players must be a positive integer")
    require(abs(sum(t["share"] for t in tiers) - 1) < 1e-12, "Tier shares must sum to 1")
    for tier in tiers:
        require(tier["share"] >= 0 and tier["paid_pulls_per_standard_span"] >= 0,
                "Negative tier share or budget")
        count = population["n_players"] * tier["share"]
        require(abs(count - round(count)) < 1e-8, "Tier count is fractional; define allocation")
    require(set(tier_ids) == set(behavior["tier_policy"]), "Tier policy coverage differs")
    require(population["core_retention_tier"] in tier_ids, "Unknown core retention tier")
    require(behavior["temperature"] > 0, "Temperature must be positive")
    require(behavior["full_star_threshold"] == 1, "Full-star threshold is contract-fixed at 1")
    for policy in behavior["tier_policy"].values():
        require(0 <= policy["initial_hoarding"] <= 1, "Invalid hoarding initial value")
        require(policy["dps_target"] in configs["gacha"]["target_paths"], "Unknown DPS goal")
    combat, economy, gacha = configs["combat"], configs["economy"], configs["gacha"]
    for field in ("attacker_level_coefficient", "enemy_defense", "hp_index", "time_limit_seconds"):
        require(combat["reference"][field] > 0, f"Invalid reference {field}")
    require(combat["gear_growth"]["time_constant_days"] > 0 and
            combat["gear_growth"]["max_bonus"] >= 0, "Invalid gear growth")
    require(all(x > 0 for x in combat["investment"]["cinema_multipliers"]), "Invalid cinema multiplier")
    for name, stages in gacha["target_paths"].items():
        require(isinstance(stages, list) and stages and len(set(stages)) == len(stages),
                f"Invalid target path: {name}")
        acquired_cinema = 0
        for stage in stages:
            if stage in {"body", "signature_weapon"}:
                continue
            match = re.fullmatch(r"cinema_(\d+)", stage)
            require(match is not None, f"Unknown target stage: {stage}")
            cinema = int(match.group(1))
            require("body" in stages[:stages.index(stage)] and cinema == acquired_cinema + 1,
                    f"Cinema stages are not sequential: {name}")
            require(cinema < len(combat["investment"]["cinema_multipliers"]),
                    f"No combat multiplier for target cinema: {name}")
            acquired_cinema = cinema
    require(all(x > 0 for x in combat["shield"]["alignment_values"]), "Invalid alignment")
    require(0 <= combat["shield"]["damage_reduction"] <= 1, "Invalid shield reduction")
    require(economy["credit_minor_per_pull"] > 0 and economy["standard_span_days"] > 0,
            "Invalid currency/time unit")
    require(economy["credit_minor_per_pull"] == 160 and economy["standard_span_days"] == 42,
            "Currency/time units are contract-fixed; update the contract for unit changes")
    require(economy["subscription"]["renewal_days"] > 0, "Invalid subscription interval")
    for name, pool in gacha["pools"].items():
        require(0 <= pool["base_s_probability"] <= 1, f"Invalid {name} base probability")
        require(0 <= pool["up_given_s"] <= 1, f"Invalid {name} UP probability")
        require(isinstance(pool["hard_pity"], int) and isinstance(pool["soft_pity_start"], int),
                "Pity boundaries must be integers")
        require(1 <= pool["soft_pity_start"] <= pool["hard_pity"], "Invalid pity boundary")
        require(pool["soft_increment"] >= 0, "Negative soft-pity increment")
    scenario = configs["scenarios"]
    periods = scenario["timeline"]["n_periods"]
    require(periods > 0 and scenario["timeline"]["days_per_period"] > 0, "Invalid timeline")
    keys = set()
    for event in scenario["events"]:
        require(1 <= event["t"] <= periods, "Event outside timeline")
        require(event["banner_id"] not in keys, "Duplicate banner_id")
        keys.add(event["banner_id"])
        require(0 <= event["theta"] <= 1 and 0 <= event["uptime"] <= 1, "Invalid event environment")
        if event["role"] == "support":
            require(event["unit_id"] in combat["support"]["gains"], "Support has no gain")
    experiments = configs["experiments"]
    train, validation = experiments["train_seeds"], experiments["validation_seeds"]
    require(train and validation, "Both seed splits must be non-empty")
    require(len(set(train)) == len(train) and len(set(validation)) == len(validation), "Repeated seed")
    require(not set(train).intersection(validation), "Training/validation seed leakage")
    for strength in experiments["grid"]["mechanism_strength"]:
        require(strength >= 0 and combat["shield"]["damage_reduction"] * strength <= 1,
                "Grid strength produces invalid shield reduction")
    require(all(x >= 0 for x in experiments["grid"]["support_gain_scale"]), "Negative support scale")
    rng = configs["randomness"]
    require(rng["uint_bytes"] * 8 - rng["discard_low_bits"] == rng["uniform_bits"],
            "RNG bit mapping is inconsistent")
    return owners


def flatten(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            if key not in {"description", "provenance"}:
                yield from flatten(child, f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, list) and any(isinstance(x, dict) for x in value):
        for index, child in enumerate(value):
            yield from flatten(child, f"{prefix}[{index}]")
    else:
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        yield prefix, rendered.replace("|", "\\|")


def render_defaults(manifest, profile, configs, owners):
    lines = ["# V1基线默认参数视图（自动生成）", "",
             "> 本视图来自 `config/experiment.json` 及其模块JSON；修改这些JSON后运行 `python tools/design_docs.py sync`。本文件不是参数维护源。", "",
             "V2 已实现并完成实证接入与跨种子复核。当前研究使用独立的[V2配置入口](../../config/research_v2/experiment.json)，公式增量见[V2设计](research_v2.md)，本表保留V1基线参数，不代表当前主实验。参数中的教学假设不表示官方规则或真实玩家参数。", ""]
    for name, relative in profile["modules"].items():
        path = local(relative, PROFILE.parent).relative_to(ROOT).as_posix()
        owner = owners[path]
        lines += [f"## {name} — {owner}", "", f"维护文件：[{path}](../../{path})", "",
                  "| 参数路径 | 当前值 |", "| --- | --- |"]
        lines.extend(f"| `{key}` | `{value}` |" for key, value in flatten(configs[name]))
        lines.append("")
    e = configs["experiments"]
    points = len(e["grid"]["mechanism_strength"]) * len(e["grid"]["support_gain_scale"])
    main = points * (len(e["train_seeds"]) + len(e["validation_seeds"]))
    nonbaseline = len([x for x in e["pain_sensitivity"]["factors"] if x != 1])
    extra = len(e["pain_sensitivity"]["parameters"]) * nonbaseline * points * len(e["validation_seeds"])
    n, t = configs["population"]["n_players"], configs["scenarios"]["timeline"]["n_periods"]
    lines += ["## V1基线执行规模（从配置推导）", "",
              f"- 候选点：{points}；主网格逻辑run：{main}。",
              f"- 单因素痛感扰动新增run：{extra}；去重网格总run：{main + extra}。",
              f"- 单run生命周期记录：{n * t:,}；网格总生命周期记录：{(main + extra) * n * t:,}。",
              "- 不包含另行参照、可选扩展或重跑attempt；全库存与逐抽日志按实际生成量计。", ""]
    return "\n".join(lines)


def render_index(manifest):
    lines = ["# 模块索引（自动生成）", "", "> 维护module_manifest.json后运行sync。", "",
             "| 模块 | 设计文档 | 参数维护文件 |", "| --- | --- | --- |"]
    for m in manifest["modules"]:
        doc = Path(m["document"]).relative_to("docs/design").as_posix()
        configs = "、".join(f"[{Path(p).name}](../../{p})" for p in m["configuration_files"]) or "引用所属模块"
        lines.append(f"| {m['id']} | [{m['title']}]({doc}) | {configs} |")
    lines += ["", "## 旧章节迁移", "", "| v1.0章节 | 当前权威模块 |", "| --- | --- |"]
    for m in manifest["modules"]:
        if m["legacy_sections"]:
            doc = Path(m["document"]).relative_to("docs/design").as_posix()
            lines.append(f"| {', '.join(map(str, m['legacy_sections']))} | [{m['id']}]({doc}) |")
    return "\n".join(lines) + "\n"


def generated(manifest, profile, configs, owners):
    return {DESIGN / "defaults.md": render_defaults(manifest, profile, configs, owners),
            DESIGN / "module_index.md": render_index(manifest)}


def check_links():
    paths = [ROOT / "prompt_spec.md"] + list(DESIGN.rglob("*.md"))
    count = 0
    for path in paths:
        if path != ROOT / "prompt_spec.md" and "archive" in path.relative_to(DESIGN).parts:
            continue
        text = path.read_text(encoding="utf-8-sig")
        require("§" not in text, f"Unreplaced marker in {path}")
        fences = re.findall(r"^```", text, flags=re.MULTILINE)
        require(len(fences) % 2 == 0, f"Unbalanced code fences: {path}")
        for match in markdown_links(text):
            target = match.group(1)
            target = target.strip().strip("<>")
            if re.match(r"^(?:[a-zA-Z][\w+.-]*:|//|#)", target):
                continue
            target = unquote(target.split("#", 1)[0])
            if target:
                require(local(target, path.parent).exists(), f"Broken link: {path}: {target}")
        count += 1
    return count


def assemble(manifest):
    output = DESIGN / "generated" / "full_design.md"
    output.parent.mkdir(exist_ok=True)
    sources = [DESIGN / "architecture.md"] + [local(m["document"]) for m in manifest["modules"]] + [DESIGN / "defaults.md", DESIGN / "change_guide.md"]
    pieces = ["# 完整设计阅读版（自动生成）\n\n> V2 已实现并完成实证接入与跨种子复核。本文件汇总模块公共设计与V1基线参数；V2增量见[V2设计](../research_v2.md)，当前结果见[总报告](../../../outputs/final_release/report/REPORT.md)。请修改模块文档和JSON，不直接修改本文件。\n"]
    for path in sources:
        text = path.read_text(encoding="utf-8-sig")
        def rebase(match):
            target = match.group(1)
            if re.match(r"^(?:[a-zA-Z][\w+.-]*:|//|#)", target):
                return match.group(0)
            import os
            plain, _, anchor = target.partition("#")
            relative = Path(os.path.relpath(local(unquote(plain), path.parent), output.parent)).as_posix()
            return match.group(0).replace(target, relative + ("#" + anchor if anchor else ""))
        pieces.append(replace_links(text, rebase))
    output.write_text("\n\n---\n\n".join(pieces), encoding="utf-8")
    print(f"Generated {output.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sync", "check", "impact", "assemble"])
    parser.add_argument("module_id", nargs="?")
    args = parser.parse_args()
    manifest, profile, configs = load()
    owners = validate(manifest, profile, configs)
    expected = generated(manifest, profile, configs, owners)
    if args.command == "sync":
        for path, text in expected.items():
            path.write_text(text, encoding="utf-8")
        print("Synced parameter view and module index")
    elif args.command == "check":
        for path, text in expected.items():
            require(path.is_file() and path.read_text(encoding="utf-8-sig") == text,
                    f"Generated view is stale: {path}; run sync")
        count = check_links()
        print(f"PASS: {len(manifest['modules'])} modules, {len(configs) + 1} configuration files, {count} active Markdown files; links, DAG and selected configuration invariants checked")
    elif args.command == "assemble":
        assemble(manifest)
    else:
        ids = {m["id"] for m in manifest["modules"]}
        require(args.module_id in ids, "Provide a registered module ID")
        affected, queue = {args.module_id}, deque([args.module_id])
        while queue:
            current = queue.popleft()
            for m in manifest["modules"]:
                if current in m["dependencies"] + m.get("consumes_outputs_of", []) and m["id"] not in affected:
                    affected.add(m["id"])
                    queue.append(m["id"])
        print("\n".join(m["id"] + ": " + m["title"] for m in manifest["modules"] if m["id"] in affected))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except (ValueError, KeyError, OSError, TypeError) as exc:
        raise SystemExit(f"FAIL: {exc}")
