from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EpisodeJsonlLogger:
    def __init__(self, log_dir: Path, enabled: bool = True):
        self.enabled = bool(enabled)
        self.log_dir = Path(log_dir)
        self.path: Path | None = None
        self.run_dir: Path | None = None
        self.events_path: Path | None = None
        self.steps_path: Path | None = None
        self.manifest_path: Path | None = None
        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.path = self.log_dir / f"episodes_{stamp}.jsonl"
            self.run_dir = self.log_dir / f"run_{stamp}"
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.events_path = self.run_dir / "events.jsonl"
            self.steps_path = self.run_dir / "steps.jsonl"
            self.manifest_path = self.run_dir / "manifest.json"

    def write(self, event: str, payload: dict[str, Any]) -> None:
        if not self.enabled or self.path is None:
            return
        record = {
            "event": event,
            "logged_at": utc_now_iso(),
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        if self.events_path is not None:
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def write_step(self, payload: dict[str, Any]) -> None:
        if not self.enabled or self.steps_path is None:
            return
        record = {
            "event": "step",
            "logged_at": utc_now_iso(),
            **payload,
        }
        with self.steps_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def write_manifest(self, payload: dict[str, Any]) -> None:
        if not self.enabled or self.manifest_path is None:
            return
        record = {
            "created_at": utc_now_iso(),
            **payload,
        }
        self.manifest_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
