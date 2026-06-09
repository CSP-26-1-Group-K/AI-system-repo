from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def legacy_targets(log_dir: Path) -> list[Path]:
    targets: list[Path] = []
    targets.extend(sorted(log_dir.glob("episodes_*.jsonl")))
    targets.extend(sorted(path for path in log_dir.glob("run_*") if path.is_dir()))
    return targets


def organize_legacy_logs(log_dir: Path, apply: bool) -> dict:
    log_dir = Path(log_dir)
    legacy_dir = log_dir / "legacy"
    targets = legacy_targets(log_dir)
    operations = []
    if apply:
        legacy_dir.mkdir(parents=True, exist_ok=True)
    for src in targets:
        dst = legacy_dir / src.name
        operations.append({"source": str(src), "destination": str(dst), "kind": "dir" if src.is_dir() else "file"})
        if apply:
            if dst.exists():
                raise FileExistsError(f"Legacy target already exists: {dst}")
            shutil.move(str(src), str(dst))
    summary = {
        "created_at": utc_now_iso(),
        "log_dir": str(log_dir),
        "legacy_dir": str(legacy_dir),
        "applied": bool(apply),
        "moved_count": len(operations) if apply else 0,
        "candidate_count": len(operations),
        "operations": operations,
    }
    if apply:
        manifest_path = legacy_dir / "LEGACY_MANIFEST.json"
        manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary["manifest"] = str(manifest_path)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Move old HomeSense flat/run logs into a legacy folder.")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs/homesense_episodes"),
        help="HomeSense episode log directory.",
    )
    parser.add_argument("--apply", action="store_true", help="Actually move files. Without this flag, only prints a plan.")
    args = parser.parse_args()
    print(json.dumps(organize_legacy_logs(args.log_dir, apply=args.apply), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
