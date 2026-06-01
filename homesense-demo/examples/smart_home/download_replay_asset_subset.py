#!/usr/bin/env python3
"""Download the BEHAVIOR asset subset required by one replay file.

The requirements JSON is generated from the replay metadata and scene JSON.
It intentionally downloads exact scene/object folders instead of the full
BEHAVIOR-1K asset dataset.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download
from huggingface_hub.utils import HfHubHTTPError

try:
    from huggingface_hub import get_token
except ImportError:  # pragma: no cover - compatibility with older huggingface_hub
    get_token = None


REPO_ID = "behavior-1k/behavior-1k-assets"
DEFAULT_LOCAL_DIR = Path("datasets") / "behavior-1k-assets"
COMMON_PATTERNS = [
    "VERSION",
    "metadata/**",
    "systems/**",
]


def _has_token() -> bool:
    return bool(get_token and get_token())


def _check_repo_access() -> None:
    api = HfApi()
    next(iter(api.list_repo_tree(REPO_ID, repo_type="dataset", recursive=False)))


def _load_requirements(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("scene_model"):
        raise ValueError(f"{path} does not include scene_model")
    if not isinstance(data.get("objects"), list):
        raise ValueError(f"{path} does not include objects[]")
    return data


def build_allow_patterns(requirements: dict) -> list[str]:
    scene_model = requirements["scene_model"]
    patterns = [
        *COMMON_PATTERNS,
        f"scenes/{scene_model}/**",
    ]

    object_patterns = set()
    for obj in requirements["objects"]:
        category = obj.get("category")
        model = obj.get("model")
        if category and model:
            object_patterns.add(f"objects/{category}/{model}/**")

    return [*patterns, *sorted(object_patterns)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download only the BEHAVIOR assets required by a replay."
    )
    parser.add_argument(
        "requirements",
        type=Path,
        help="Replay asset requirements JSON generated from the replay scene.",
    )
    parser.add_argument(
        "--local-dir",
        type=Path,
        default=DEFAULT_LOCAL_DIR,
        help=f"Target behavior-1k-assets directory. Default: {DEFAULT_LOCAL_DIR}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print allow_patterns and exit without contacting Hugging Face.",
    )
    parser.add_argument(
        "--check-access",
        action="store_true",
        help="Verify Hugging Face token/repo access without downloading files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    requirements = _load_requirements(args.requirements)
    patterns = build_allow_patterns(requirements)

    print(f"Replay: {requirements.get('replay_file', '<unknown>')}")
    print(f"Scene: {requirements['scene_model']}")
    print(f"Object folders: {max(0, len(patterns) - len(COMMON_PATTERNS) - 1)}")
    print("Allowed patterns:")
    for pattern in patterns:
        print(f"  - {pattern}")

    if args.dry_run:
        return 0

    if not _has_token():
        print(
            "\nNo Hugging Face token is configured for this environment.\n"
            "Run:\n"
            "  /home/user/Desktop/isaac-sim-5.1/python.sh -c \"from huggingface_hub import login; login()\"\n",
            file=sys.stderr,
        )
        return 2

    try:
        _check_repo_access()
    except HfHubHTTPError as exc:
        print(
            f"\nCannot access {REPO_ID}: {exc}\n"
            "Confirm the Hugging Face account has accepted the dataset/license access.",
            file=sys.stderr,
        )
        return 3

    if args.check_access:
        print(f"\nAccess OK: {REPO_ID}")
        return 0

    snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        local_dir=args.local_dir,
        allow_patterns=patterns,
    )
    print("\nReplay asset subset download complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
