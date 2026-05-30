from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ContextBaseline:
    activity_to_zone: dict[str, str] = field(default_factory=dict)
    activity_to_task_hint: dict[str, str] = field(default_factory=dict)
    default_zone: str = "unknown"

    def predict(self, activity_id: str | None, fallback_zone: str | None = None) -> dict[str, Any]:
        key = activity_id or "__none__"
        zone = self.activity_to_zone.get(key) or fallback_zone or self.default_zone
        task_hint = self.activity_to_task_hint.get(key) or "observe"
        return {
            "resident_zone_estimate": zone,
            "task_hint": task_hint,
            "source": "context_baseline_v1",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_type": "context_baseline_v1",
            "activity_to_zone": self.activity_to_zone,
            "activity_to_task_hint": self.activity_to_task_hint,
            "default_zone": self.default_zone,
        }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def infer_task_hint(activity_id: str | None, zone: str | None) -> str:
    if activity_id in {"doing_laundry", "checking_laundry"} or zone == "utility_room":
        return "laundry"
    if activity_id in {"arriving_home", "watching_tv", "resting_on_sofa", "resting_in_bedroom"}:
        return "deliver_item"
    return "observe"


def fit_context_baseline(records: list[dict[str, Any]]) -> ContextBaseline:
    zone_counts: dict[str, Counter[str]] = defaultdict(Counter)
    task_counts: dict[str, Counter[str]] = defaultdict(Counter)
    global_zones: Counter[str] = Counter()

    for record in records:
        if record.get("event") not in {"episode_start", "step"}:
            continue
        activity_context = record.get("activity_context") or {}
        activity_id = activity_context.get("activity_id") or "__none__"
        zone = (
            record.get("resident_zone_sampled")
            or record.get("episode_zone")
            or (record.get("human") or {}).get("zone")
            or activity_context.get("ground_truth_zone")
        )
        if not zone:
            continue
        zone = str(zone)
        zone_counts[str(activity_id)][zone] += 1
        global_zones[zone] += 1
        task_counts[str(activity_id)][infer_task_hint(activity_context.get("activity_id"), zone)] += 1

    model = ContextBaseline()
    model.default_zone = global_zones.most_common(1)[0][0] if global_zones else "unknown"
    model.activity_to_zone = {
        activity_id: counts.most_common(1)[0][0]
        for activity_id, counts in zone_counts.items()
        if counts
    }
    model.activity_to_task_hint = {
        activity_id: counts.most_common(1)[0][0]
        for activity_id, counts in task_counts.items()
        if counts
    }
    return model


def save_model(model: ContextBaseline, path: Path) -> None:
    Path(path).write_text(
        json.dumps(model.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
