from __future__ import annotations

from dataclasses import asdict, dataclass, field
from time import time
from typing import Any


@dataclass
class HumanState:
    zone: str = "living_room"
    position: list[float] = field(default_factory=lambda: [2.0, 4.5, 0.0])
    heading_deg: float = 0.0


@dataclass
class MotionSensorState:
    sensor_id: str = "motion_sensor_0"
    detected: bool = True
    zone: str | None = "living_room"
    last_known_zone: str | None = "living_room"
    distance_m: float | None = 1.0
    confidence: float = 1.0
    active_sensor_id: str | None = "motion_living_room"

    def update_zone(
        self,
        zone: str | None,
        detected: bool = True,
        distance_m: float | None = None,
        sensor_id: str | None = None,
    ) -> None:
        self.detected = detected
        self.zone = zone if detected else None
        if detected and zone:
            self.last_known_zone = zone
        self.distance_m = distance_m
        if sensor_id is not None:
            self.sensor_id = sensor_id
        self.active_sensor_id = sensor_id if detected else None
        self.confidence = 1.0 if detected else 0.0


@dataclass
class PressureSensorState:
    sensor_id: str = "pressure_sensor_0"
    weight_kg: float = 0.0
    threshold_kg: float = 6.0

    @property
    def triggered(self) -> bool:
        return self.weight_kg >= self.threshold_kg


@dataclass
class RobotState:
    status: str = "idle"
    task: str | None = None
    replay_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def busy(self) -> bool:
        return self.status in {"running_replay", "replay_selected"}


@dataclass
class SmartHomeState:
    timestamp: float = field(default_factory=time)
    human: HumanState = field(default_factory=HumanState)
    motion: MotionSensorState = field(default_factory=MotionSensorState)
    pressure: PressureSensorState = field(default_factory=PressureSensorState)
    robot: RobotState = field(default_factory=RobotState)
    camera_mode: str = "overview"

    def refresh_timestamp(self) -> None:
        self.timestamp = time()

    def to_dict(self) -> dict[str, Any]:
        self.refresh_timestamp()
        data = asdict(self)
        data["pressure"]["triggered"] = self.pressure.triggered
        data["robot"]["busy"] = self.robot.busy
        return data


@dataclass(frozen=True)
class TaskCommand:
    task: str
    requested_zone: str | None = None


@dataclass(frozen=True)
class CameraCommand:
    mode: str
