"""Refresh the integrated publication from completed local study exports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zzz_research.publication_data import prepare_publication
from zzz_research.report import build_report
from zzz_research.dashboard_build import build_dashboard


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path("outputs/final_release"))
    args = parser.parse_args(argv)
    project = Path(__file__).resolve().parents[1]
    out = args.dir if args.dir.is_absolute() else project / args.dir
    publication = prepare_publication(project, out)
    report = build_report(out)
    dashboard = build_dashboard(out)
    print(json.dumps({"config_id": publication["base_config_id"],
                      "report": report, "dashboard": dashboard}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
