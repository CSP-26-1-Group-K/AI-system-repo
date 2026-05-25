from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from smart_home.service_types import SmartHomeState


DEFAULT_ZONE_ORDER = ("living_room", "kitchen", "bedroom")


@dataclass(frozen=True)
class SensorEncoderConfig:
    zone_order: tuple[str, ...] = DEFAULT_ZONE_ORDER
    pressure_max_kg: float = 12.0


class SensorStateEncoder:
    def __init__(self, config: SensorEncoderConfig | None = None):
        self.config = config or SensorEncoderConfig()

    @property
    def labels(self) -> list[str]:
        labels = [f"{zone}_motion_detected" for zone in self.config.zone_order]
        labels.extend(["laundry_pressure_weight_normalized", "laundry_pressure_triggered"])
        return labels

    def encode(self, state: SmartHomeState | dict[str, Any]) -> list[float]:
        motion, pressure = self._extract(state)
        detected_zone = motion.get("zone") if motion.get("detected") else None
        vector = [1.0 if detected_zone == zone else 0.0 for zone in self.config.zone_order]

        weight_kg = float(pressure.get("weight_kg", pressure.get("estimated_weight_kg", 0.0)) or 0.0)
        pressure_max = max(float(self.config.pressure_max_kg), 1e-6)
        vector.append(max(0.0, min(1.0, weight_kg / pressure_max)))
        vector.append(1.0 if bool(pressure.get("triggered")) else 0.0)
        return vector

    def encode_many(self, states: Iterable[SmartHomeState | dict[str, Any]]) -> list[list[float]]:
        return [self.encode(state) for state in states]

    def _extract(self, state: SmartHomeState | dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if isinstance(state, SmartHomeState):
            data = state.to_dict()
        else:
            data = state
        return dict(data.get("motion", {})), dict(data.get("pressure", {}))
