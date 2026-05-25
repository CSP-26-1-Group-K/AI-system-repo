#!/usr/bin/env python3
"""Download only the BEHAVIOR scene asset subset needed by the live demo."""

from __future__ import annotations

import argparse
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
DEFAULT_PATTERNS = ["scenes/**", "metadata/**", "VERSION"]


def _has_token() -> bool:
    return bool(get_token and get_token())


def _check_repo_access() -> None:
    api = HfApi()
    # A shallow tree request is enough to verify auth/license access without
    # downloading asset files.
    next(iter(api.list_repo_tree(REPO_ID, repo_type="dataset", recursive=False)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download the BEHAVIOR-1K scene subset from Hugging Face. "
            "This intentionally avoids the full setup.sh --dataset path."
        )
    )
    parser.add_argument(
        "--local-dir",
        type=Path,
        default=DEFAULT_LOCAL_DIR,
        help=f"Target behavior-1k-assets directory. Default: {DEFAULT_LOCAL_DIR}",
    )
    parser.add_argument(
        "--pattern",
        action="append",
        dest="patterns",
        help=(
            "Extra allow_pattern passed to snapshot_download. "
            "Can be repeated, e.g. --pattern 'systems/**'."
        ),
    )
    parser.add_argument(
        "--check-access",
        action="store_true",
        help="Only verify Hugging Face token/repo access; do not download files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not _has_token():
        print(
            "No Hugging Face token is configured for this environment.\n"
            "Run:\n"
            "  /home/user/Desktop/isaac-sim-5.1/python.sh -c \"from huggingface_hub import login; login()\"\n"
            "\n"
            "Then rerun this script.",
            file=sys.stderr,
        )
        return 2

    try:
        _check_repo_access()
    except HfHubHTTPError as exc:
        print(
            f"Cannot access {REPO_ID}: {exc}\n"
            "Confirm the Hugging Face account has accepted the dataset/license access.",
            file=sys.stderr,
        )
        return 3

    if args.check_access:
        print(f"Access OK: {REPO_ID}")
        return 0

    patterns = [*DEFAULT_PATTERNS, *(args.patterns or [])]
    print(f"Downloading {REPO_ID} to {args.local_dir}")
    print("Allowed patterns:")
    for pattern in patterns:
        print(f"  - {pattern}")

    snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        local_dir=args.local_dir,
        allow_patterns=patterns,
    )
    print("Scene asset subset download complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
