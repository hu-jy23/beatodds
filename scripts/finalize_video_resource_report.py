#!/usr/bin/env python3
"""Finalize an already-downloaded video resource into report artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from beatodds.agents.video_reporter import finalize_video_resource_report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从已有视频 resource_dir 生成 video_report.pdf/evidence_card.md。"
    )
    parser.add_argument("--resource-dir", required=True)
    args = parser.parse_args()

    result = finalize_video_resource_report(Path(args.resource_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
