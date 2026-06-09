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
        self.legacy_dir: Path | None = None
        self.path: Path | None = None
        self.run_dir: Path | None = None
        self.events_path: Path | None = None
        self.steps_path: Path | None = None
        self.manifest_path: Path | None = None
        self.metadata_path: Path | None = None
        self.annotations_path: Path | None = None
        self.quality_report_path: Path | None = None
        self.camera_dirs: dict[str, Path] = {}
        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self.legacy_dir = self.log_dir / "legacy"
            self.legacy_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.path = self.legacy_dir / f"episodes_{stamp}.jsonl"
            self.run_dir = self.log_dir / f"run_{stamp}"
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.events_path = self.run_dir / "events.jsonl"
            self.steps_path = self.run_dir / "steps.jsonl"
            self.manifest_path = self.run_dir / "manifest.json"
            self.metadata_path = self.run_dir / "metadata.json"
            self.annotations_path = self.run_dir / "annotations.json"
            self.quality_report_path = self.run_dir / "quality_report.json"
            cameras_dir = self.run_dir / "cameras"
            self.camera_dirs = {
                "top": cameras_dir / "top",
                "robot": cameras_dir / "robot",
            }
            for camera_dir in self.camera_dirs.values():
                camera_dir.mkdir(parents=True, exist_ok=True)

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
        text = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.manifest_path.write_text(text, encoding="utf-8")
        if self.metadata_path is not None:
            self.metadata_path.write_text(text, encoding="utf-8")
        self.write_annotations({})
        self.write_quality_report({})

    def write_annotations(self, payload: dict[str, Any]) -> None:
        if not self.enabled or self.annotations_path is None:
            return
        record = {
            "schema_version": "homesense_annotations_v1",
            "updated_at": utc_now_iso(),
            "success": None,
            "failure_reason": None,
            "annotated_by": None,
            "notes": "",
            **payload,
        }
        self.annotations_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_quality_report(self, payload: dict[str, Any]) -> None:
        if not self.enabled or self.quality_report_path is None:
            return
        record = {
            "schema_version": "homesense_quality_report_v1",
            "updated_at": utc_now_iso(),
            "status": "in_progress",
            "episode_count": 0,
            "step_count": 0,
            "missing_frame_ratio": None,
            "missing_state_ratio": None,
            "success_count": None,
            "failure_count": None,
            **payload,
        }
        self.quality_report_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
