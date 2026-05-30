from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smart_home.replay_import import issues_to_dicts, validate_replay_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path, help="Path to collaborator replay import manifest JSON.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable validation result.")
    parser.add_argument("--skip-path-check", action="store_true", help="Validate manifest shape without requiring replay files to exist.")
    args = parser.parse_args()

    issues = validate_replay_manifest(args.manifest, check_paths=not args.skip_path_check)
    errors = [issue for issue in issues if issue.severity == "error"]
    if args.json:
        print(json.dumps({"ok": not errors, "issues": issues_to_dicts(issues)}, ensure_ascii=False, indent=2))
    else:
        if not issues:
            print("Replay import manifest is valid.")
        for issue in issues:
            replay = f" replay={issue.replay_id}" if issue.replay_id else ""
            print(f"[{issue.severity}]{replay} {issue.message}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
