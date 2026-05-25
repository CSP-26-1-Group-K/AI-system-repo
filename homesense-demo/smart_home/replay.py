from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smart_home.service_types import SmartHomeState, TaskCommand


class ReplaySelectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplaySpec:
    replay_id: str
    task: str
    path: str
    trigger: dict[str, Any]
    initial_state: str = "home_default"
    final_state: str = "home_default"
    duration_s: float | None = None


class ReplayRegistry:
    def __init__(self, replay_specs: list[ReplaySpec]):
        self._by_id = {spec.replay_id: spec for spec in replay_specs}

    @classmethod
    def from_json(cls, path: str | Path) -> "ReplayRegistry":
        registry_path = Path(path)
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        specs = [ReplaySpec(**item) for item in data.get("replays", [])]
        return cls(specs)

    def get(self, replay_id: str) -> ReplaySpec:
        try:
            return self._by_id[replay_id]
        except KeyError as exc:
            raise ReplaySelectionError(f"Replay not found: {replay_id}") from exc

    def select(self, command: TaskCommand, state: SmartHomeState) -> ReplaySpec:
        if command.task == "deliver_item":
            return self._select_delivery(command, state)
        if command.task == "laundry":
            return self._select_laundry(state)
        raise ReplaySelectionError(f"Unsupported task: {command.task}")

    def _select_delivery(self, command: TaskCommand, state: SmartHomeState) -> ReplaySpec:
        zone = command.requested_zone or state.motion.zone or state.motion.last_known_zone
        if not zone:
            raise ReplaySelectionError("No resident zone available for delivery task")
        return self._find_by_trigger("deliver_item", {"resident_zone": zone})

    def _select_laundry(self, state: SmartHomeState) -> ReplaySpec:
        if not state.pressure.triggered:
            raise ReplaySelectionError("Laundry basket pressure threshold is not triggered")
        return self._find_by_trigger("laundry", {"pressure_triggered": True})

    def _find_by_trigger(self, task: str, trigger: dict[str, Any]) -> ReplaySpec:
        for spec in self._by_id.values():
            if spec.task != task:
                continue
            if all(spec.trigger.get(key) == value for key, value in trigger.items()):
                return spec
        raise ReplaySelectionError(f"No replay registered for task={task}, trigger={trigger}")

    def to_dict(self) -> dict[str, Any]:
        return {"replays": [spec.__dict__ for spec in self._by_id.values()]}
