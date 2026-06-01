from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smart_home.dataset_summary import summarize_dataset


def main():
    parser = argparse.ArgumentParser(description="Summarize HomeSense episode JSONL datasets.")
    parser.add_argument(
        "path",
        type=Path,
        help="Run directory containing events.jsonl / steps.jsonl, or a single JSONL file.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON summary output path.")
    args = parser.parse_args()

    summary = summarize_dataset(args.path)
    text = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
