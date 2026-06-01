from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                yield {
                    "event": "__decode_error__",
                    "path": str(path),
                    "line": line_no,
                    "reason": str(exc),
                }


def dataset_files(path: Path) -> list[Path]:
    path = Path(path)
    if path.is_file():
        return [path]
    candidates = [path / "events.jsonl", path / "steps.jsonl"]
    found = [candidate for candidate in candidates if candidate.exists()]
    if found:
        return found
    return sorted(path.glob("*.jsonl"))


def _counter_dict(counter: Counter) -> dict[str, int]:
    return {str(key): int(value) for key, value in counter.most_common()}


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def summarize_dataset(path: Path) -> dict[str, Any]:
    files = dataset_files(path)
    event_counts: Counter = Counter()
    episode_ids: set[int] = set()
    zone_counts: Counter = Counter()
    estimate_zone_counts: Counter = Counter()
    scenario_counts: Counter = Counter()
    activity_counts: Counter = Counter()
    risk_counts: Counter = Counter()
    fault_counts: Counter = Counter()
    step_count = 0
    motion_known = 0
    motion_detected = 0
    motion_dropout = 0
    context_valid = 0
    task_selection_valid = 0
    safety_valid = 0
    behavior_cloning_valid = 0
    decode_errors = []

    for file_path in files:
        for record in iter_jsonl(file_path):
            event = record.get("event", "unknown")
            event_counts[event] += 1
            if event == "__decode_error__":
                decode_errors.append(record)
                continue
            episode_id = record.get("episode_id")
            if isinstance(episode_id, int):
                episode_ids.add(episode_id)
            scenario = record.get("scenario_type")
            if scenario:
                scenario_counts[scenario] += 1
            ground_truth = record.get("ground_truth") or {}
            estimates = record.get("estimates") or {}
            sensor_quality = record.get("sensor_quality") or {}
            risk = record.get("risk") or {}
            validity = record.get("training_validity") or {}
            activity_context = record.get("activity_context") or {}

            zone = ground_truth.get("resident_zone") or record.get("resident_zone_sampled") or record.get("episode_zone")
            if zone:
                zone_counts[zone] += 1
            estimate_zone = estimates.get("resident_zone") or (record.get("human") or {}).get("zone")
            if estimate_zone:
                estimate_zone_counts[estimate_zone] += 1
            activity_id = ground_truth.get("activity_id") or activity_context.get("activity_id")
            if activity_id:
                activity_counts[activity_id] += 1
            risk_level = risk.get("level")
            if risk_level:
                risk_counts[risk_level] += 1
            for fault in sensor_quality.get("sensor_faults") or []:
                fault_counts[fault] += 1

            if event == "step":
                step_count += 1
                motion = record.get("motion") or {}
                if "detected" in motion:
                    motion_known += 1
                    if motion.get("detected"):
                        motion_detected += 1
                if sensor_quality.get("motion_dropout"):
                    motion_dropout += 1
                if validity.get("context_model"):
                    context_valid += 1
                if validity.get("task_selection"):
                    task_selection_valid += 1
                if validity.get("safety_eval"):
                    safety_valid += 1
                if validity.get("policy_behavior_cloning"):
                    behavior_cloning_valid += 1

    return {
        "path": str(path),
        "files": [str(file_path) for file_path in files],
        "total_records": int(sum(event_counts.values())),
        "event_counts": _counter_dict(event_counts),
        "episode_count": len(episode_ids),
        "step_count": step_count,
        "ground_truth_zone_distribution": _counter_dict(zone_counts),
        "estimated_zone_distribution": _counter_dict(estimate_zone_counts),
        "scenario_distribution": _counter_dict(scenario_counts),
        "activity_distribution": _counter_dict(activity_counts),
        "risk_distribution": _counter_dict(risk_counts),
        "sensor_fault_distribution": _counter_dict(fault_counts),
        "motion_detection_rate": _rate(motion_detected, motion_known),
        "motion_dropout_rate": _rate(motion_dropout, step_count),
        "training_validity": {
            "context_model_rate": _rate(context_valid, step_count),
            "task_selection_rate": _rate(task_selection_valid, step_count),
            "safety_eval_rate": _rate(safety_valid, step_count),
            "policy_behavior_cloning_rate": _rate(behavior_cloning_valid, step_count),
        },
        "decode_error_count": len(decode_errors),
        "decode_errors": decode_errors[:10],
    }
