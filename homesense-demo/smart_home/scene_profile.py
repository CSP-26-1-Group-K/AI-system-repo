from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SceneProfile:
    scene_model: str
    human_start_pos: tuple[float, float, float]
    robot: dict[str, Any] = field(default_factory=dict)
    motion_sensors: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    sensor_layouts: dict[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    pressure_sensors: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    zones: dict[str, dict[str, Any]] = field(default_factory=dict)
    encoder: dict[str, Any] = field(default_factory=dict)
    doorless_scene_file: str | None = None
    doorless_portals: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    door_object_names: tuple[str, ...] = field(default_factory=tuple)
    overview_camera: dict[str, Any] | None = None
    ceiling_model_ids: tuple[str, ...] = field(default_factory=tuple)
    activity_profiles: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    demo_objects: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def primary_pressure_sensor(self) -> dict[str, Any] | None:
        return self.pressure_sensors[0] if self.pressure_sensors else None

    @property
    def zone_names(self) -> tuple[str, ...]:
        return tuple(self.zones)


def _tuple3(value: Any, *, field_name: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{field_name} must be a 3-value list")
    return (float(value[0]), float(value[1]), float(value[2]))


def _motion_sensor_spec(raw: dict[str, Any]) -> dict[str, Any]:
    spec = dict(raw)
    if "name" not in spec and "sensor_id" in spec:
        spec["name"] = spec["sensor_id"]
    if "name" not in spec:
        raise ValueError("motion sensor requires name or sensor_id")
    spec["position"] = [float(v) for v in spec["position"]]
    spec["yaw_deg"] = float(spec.get("yaw_deg", 0.0))
    spec["range_m"] = float(spec.get("range_m", 4.0))
    spec["fov_deg"] = float(spec.get("fov_deg", 90.0))
    return spec


def _pressure_sensor_spec(raw: dict[str, Any]) -> dict[str, Any]:
    spec = dict(raw)
    if "name" not in spec and "sensor_id" in spec:
        spec["name"] = spec["sensor_id"]
    spec["position"] = [float(v) for v in spec["position"]]
    spec["size"] = [float(v) for v in spec.get("size", [0.9, 0.9, 0.03])]
    spec["threshold_kg"] = float(spec.get("threshold_kg", 6.0))
    return spec


def load_scene_profile(repo_root: Path, scene_model: str) -> SceneProfile | None:
    config_path = repo_root / "smart_home" / "configs" / "scenes" / f"{scene_model.lower()}.yaml"
    if not config_path.exists():
        return None
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    configured_model = str(data.get("scene_model") or data.get("scene") or scene_model)
    if configured_model != scene_model:
        raise ValueError(f"Scene profile {config_path} declares {configured_model}, expected {scene_model}")

    human = data.get("resident") or data.get("human") or {}
    if "start_position" not in human:
        raise ValueError(f"Scene profile {config_path} is missing resident.start_position")

    return SceneProfile(
        scene_model=scene_model,
        human_start_pos=_tuple3(human["start_position"], field_name="resident.start_position"),
        robot=dict(data.get("robot") or {}),
        motion_sensors=tuple(_motion_sensor_spec(spec) for spec in data.get("motion_sensors", [])),
        sensor_layouts={
            str(name): tuple(_motion_sensor_spec(spec) for spec in specs)
            for name, specs in (data.get("sensor_layouts") or {}).items()
        },
        pressure_sensors=tuple(_pressure_sensor_spec(spec) for spec in data.get("pressure_sensors", [])),
        zones=dict(data.get("zones") or {}),
        encoder=dict(data.get("encoder") or {}),
        doorless_scene_file=data.get("doorless_scene_file"),
        doorless_portals=tuple(dict(portal) for portal in data.get("doorless_portals", [])),
        door_object_names=tuple(str(name) for name in data.get("door_object_names", [])),
        overview_camera=dict(data["overview_camera"]) if data.get("overview_camera") else None,
        ceiling_model_ids=tuple(str(model_id).lower() for model_id in data.get("ceiling_model_ids", [])),
        activity_profiles=dict(data.get("activity_profiles") or {}),
        demo_objects=tuple(dict(obj) for obj in data.get("demo_objects", [])),
    )
