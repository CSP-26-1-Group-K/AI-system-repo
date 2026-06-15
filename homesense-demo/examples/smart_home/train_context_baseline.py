from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smart_home.context_baseline import fit_context_baseline, load_jsonl, save_model


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path, help="events.jsonl, steps.jsonl, or legacy episodes_*.jsonl file.")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT.parent / "datasets/homesense_episodes/context_baseline_model.json",
        help="Output model JSON path.",
    )
    parser.add_argument("--predict-activity", default=None, help="Optionally print a prediction for one activity id.")
    args = parser.parse_args()

    records = load_jsonl(args.jsonl)
    model = fit_context_baseline(records)
    save_model(model, args.output)
    print(f"trained_records={len(records)}")
    print(f"model={args.output}")
    print(json.dumps(model.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    if args.predict_activity is not None:
        print(json.dumps(model.predict(args.predict_activity), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
