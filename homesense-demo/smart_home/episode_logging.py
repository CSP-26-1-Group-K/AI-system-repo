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
        self.session_id: str | None = None
        self.session_events_path: Path | None = None
        self.session_metadata_path: Path | None = None
        self.base_manifest: dict[str, Any] = {}
        self.legacy_dir: Path | None = None
        self.path: Path | None = None
        self.run_dir: Path | None = None
        self.metadata_dir: Path | None = None
        self.data_dir: Path | None = None
        self.events_path: Path | None = None
        self.steps_path: Path | None = None
        self.manifest_path: Path | None = None
        self.metadata_path: Path | None = None
        self.annotations_path: Path | None = None
        self.quality_report_path: Path | None = None
        self.hdf5_path: Path | None = None
        self.camera_dirs: dict[str, Path] = {}
        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.session_id = f"session_{stamp}"
            self.session_events_path = self.log_dir / f"{self.session_id}_events.jsonl"
            self.session_metadata_path = self.log_dir / f"{self.session_id}_metadata.json"
            self.legacy_dir = self.log_dir / "legacy"
            self.legacy_dir.mkdir(parents=True, exist_ok=True)
            self.path = self.legacy_dir / f"episodes_{stamp}.jsonl"

    @staticmethod
    def safe_name(value: Any, fallback: str = "run") -> str:
        raw = str(value or fallback).strip()
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)
        safe = "_".join(part for part in safe.split("_") if part)
        return safe.strip("._-") or fallback

    def begin_run(self, metadata: dict[str, Any] | None = None, name_parts: list[Any] | None = None) -> Path | None:
        if not self.enabled:
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = "_".join(self.safe_name(part) for part in (name_parts or []) if part)
        run_name = f"run_{stamp}" + (f"_{suffix}" if suffix else "")
        candidate = self.log_dir / run_name
        index = 1
        while candidate.exists():
            candidate = self.log_dir / f"{run_name}_{index:02d}"
            index += 1
        self.run_dir = candidate
        self.metadata_dir = self.run_dir / "metadata"
        self.data_dir = self.run_dir / "data"
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.metadata_dir / "events.jsonl"
        self.steps_path = self.data_dir / "steps.jsonl"
        self.manifest_path = self.metadata_dir / "manifest.json"
        self.metadata_path = self.metadata_dir / "metadata.json"
        self.annotations_path = self.metadata_dir / "annotations.json"
        self.quality_report_path = self.metadata_dir / "quality_report.json"
        self.hdf5_path = self.metadata_dir / "dataset.hdf5"
        cameras_dir = self.data_dir / "cameras"
        self.camera_dirs = {
            "top": cameras_dir,
            "robot": cameras_dir,
        }
        merged = {
            **self.base_manifest,
            "dataset_unit": "task_replay_run",
            "session_id": self.session_id,
            "run_id": candidate.name,
            **(metadata or {}),
        }
        self.write_manifest(merged, update_base=False)
        return self.run_dir

    def end_run(self) -> None:
        self.export_hdf5()
        self.run_dir = None
        self.metadata_dir = None
        self.data_dir = None
        self.events_path = None
        self.steps_path = None
        self.manifest_path = None
        self.metadata_path = None
        self.annotations_path = None
        self.quality_report_path = None
        self.hdf5_path = None
        self.camera_dirs = {}

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
        if self.session_events_path is not None:
            with self.session_events_path.open("a", encoding="utf-8") as f:
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

    def write_manifest(self, payload: dict[str, Any], update_base: bool = True) -> None:
        if not self.enabled:
            return
        if update_base:
            self.base_manifest = dict(payload)
        record = {
            "created_at": utc_now_iso(),
            **payload,
        }
        text = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if self.session_metadata_path is not None and update_base:
            self.session_metadata_path.write_text(text, encoding="utf-8")
        if self.manifest_path is not None:
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

    def export_hdf5(self) -> Path | None:
        """Export one completed task run to a compact HDF5 index.

        Camera image bytes stay as JPEG files under data/cameras to avoid
        duplicating large blobs. The HDF5 stores structured JSON records,
        numeric step arrays when present, and relative image paths.
        """
        if not self.enabled or self.run_dir is None or self.hdf5_path is None:
            return None
        try:
            import h5py
            import numpy as np
        except Exception:
            return None

        def read_json(path: Path | None) -> dict[str, Any]:
            if path is None or not path.exists():
                return {}
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}

        def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
            if path is None or not path.exists():
                return []
            records: list[dict[str, Any]] = []
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        records.append({"parse_error": True, "raw": line})
            return records

        def json_text(value: Any) -> str:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)

        def write_json_dataset(group: Any, name: str, value: Any) -> None:
            group.create_dataset(name, data=json_text(value), dtype=h5py.string_dtype("utf-8"))

        def write_jsonl_dataset(group: Any, name: str, records: list[dict[str, Any]]) -> None:
            dtype = h5py.string_dtype("utf-8")
            group.create_dataset(name, data=np.asarray([json_text(record) for record in records], dtype=object), dtype=dtype)

        def nested_get(record: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
            value: Any = record
            for key in path:
                if not isinstance(value, dict) or key not in value:
                    return default
                value = value[key]
            return value

        def numeric_rows(records: list[dict[str, Any]], path: tuple[str, ...], width: int | None = None) -> np.ndarray:
            rows: list[list[float]] = []
            inferred_width = width
            for record in records:
                value = nested_get(record, path)
                if not isinstance(value, list):
                    continue
                try:
                    row = [float(item) for item in value]
                except (TypeError, ValueError):
                    continue
                if inferred_width is None:
                    inferred_width = len(row)
                if len(row) != inferred_width:
                    continue
                rows.append(row)
            return np.asarray(rows, dtype=np.float32)

        def numeric_vector(records: list[dict[str, Any]], path: tuple[str, ...]) -> np.ndarray:
            values: list[float] = []
            for record in records:
                value = nested_get(record, path)
                try:
                    values.append(float(value))
                except (TypeError, ValueError):
                    continue
            return np.asarray(values, dtype=np.float64)

        metadata = read_json(self.metadata_path)
        manifest = read_json(self.manifest_path)
        annotations = read_json(self.annotations_path)
        quality_report = read_json(self.quality_report_path)
        events = read_jsonl(self.events_path)
        steps = read_jsonl(self.steps_path)

        if self.hdf5_path.exists():
            self.hdf5_path.unlink()
        with h5py.File(self.hdf5_path, "w") as f:
            f.attrs["schema_version"] = "homesense_task_run_hdf5_v1"
            f.attrs["created_at"] = utc_now_iso()
            f.attrs["run_id"] = self.run_dir.name
            f.attrs["source_run_dir"] = str(self.run_dir)
            f.attrs["image_storage"] = "external_jpeg_relative_paths"

            meta_group = f.create_group("metadata")
            write_json_dataset(meta_group, "metadata", metadata)
            write_json_dataset(meta_group, "manifest", manifest)
            write_json_dataset(meta_group, "annotations", annotations)
            write_json_dataset(meta_group, "quality_report", quality_report)

            records_group = f.create_group("records")
            write_jsonl_dataset(records_group, "events_json", events)
            write_jsonl_dataset(records_group, "steps_json", steps)

            arrays_group = f.create_group("arrays")
            for name, path, width in [
                ("sim_time_s", ("sim_time_s",), None),
                ("wall_time_s", ("wall_time_s",), None),
                ("robot_position", ("robot_pose", "position"), 3),
                ("robot_orientation_xyzw", ("robot_pose", "orientation_xyzw"), 4),
                ("resident_position", ("resident", "state", "position"), 3),
                ("resident_velocity", ("resident", "ground_truth", "velocity"), 3),
                ("action_vector", ("action", "vector"), None),
            ]:
                data = numeric_vector(steps, path) if width is None and name in {"sim_time_s", "wall_time_s"} else numeric_rows(steps, path, width)
                arrays_group.create_dataset(name, data=data)

            camera_rows: list[dict[str, Any]] = []
            for step_index, record in enumerate(steps):
                frames_by_source = nested_get(record, ("dataset", "camera_frames"), {}) or {}
                if not isinstance(frames_by_source, dict):
                    continue
                for source, source_frames in frames_by_source.items():
                    if not isinstance(source_frames, dict):
                        continue
                    for camera_name, frame in source_frames.items():
                        if not isinstance(frame, dict) or not frame.get("available"):
                            continue
                        camera_rows.append(
                            {
                                "step_index": step_index,
                                "source": source,
                                "camera_name": camera_name,
                                "path": frame.get("path"),
                                "frame": frame.get("frame"),
                                "sequence": frame.get("sequence"),
                                "sim_time_s": frame.get("sim_time_s"),
                                "width": frame.get("width"),
                                "height": frame.get("height"),
                                "bytes": frame.get("bytes"),
                                "format": frame.get("format"),
                            }
                        )

            cameras_group = f.create_group("cameras")
            write_jsonl_dataset(cameras_group, "frames_json", camera_rows)
            string_dtype = h5py.string_dtype("utf-8")
            cameras_group.create_dataset("path", data=np.asarray([row.get("path") or "" for row in camera_rows], dtype=object), dtype=string_dtype)
            cameras_group.create_dataset(
                "camera_name",
                data=np.asarray([row.get("camera_name") or "" for row in camera_rows], dtype=object),
                dtype=string_dtype,
            )
            cameras_group.create_dataset("source", data=np.asarray([row.get("source") or "" for row in camera_rows], dtype=object), dtype=string_dtype)
            cameras_group.create_dataset("step_index", data=np.asarray([row.get("step_index", -1) for row in camera_rows], dtype=np.int64))
            cameras_group.create_dataset("sim_time_s", data=np.asarray([row.get("sim_time_s") or np.nan for row in camera_rows], dtype=np.float64))

        return self.hdf5_path
