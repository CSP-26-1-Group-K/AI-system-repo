from __future__ import annotations

import argparse
import json
import math
import queue
import random
import re
import sys
import threading
from pathlib import Path
from time import time

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch as th
import uvicorn

import omnigibson as og
import omnigibson.lazy as lazy
import omnigibson.utils.transform_utils as T
from omnigibson.macros import gm
from omnigibson.objects.dataset_object import DatasetObject
from omnigibson.objects.primitive_object import PrimitiveObject
from omnigibson.utils.constants import STRUCTURE_CATEGORIES
from omnigibson.utils.ui_utils import KeyboardEventHandler

from examples.smart_home.run_sensor_demo import (
    PRESETS,
    _clean_structure_materials,
    _get_dummy_position,
    _look_at_quat,
    _set_dummy_position,
)
from smart_home.activity import ActivitySensorSimulator, ActivityState
from smart_home.live.server import bridge
from smart_home.live.avatar import add_demo_human_avatar
from smart_home.live.constants import (
    CAMERA_PRESETS,
    CEILING_HIDDEN_CAMERA_MODES,
    CEILING_MODEL_IDS,
    HUMAN_COMMAND_LIMIT_M,
    HUMAN_MOVE_SPEED_MPS,
    VIEWPORT_CAMERA_KEYS,
    VIEWPORT_INPUT_HOLD_S,
    VIEWPORT_ROTATION_STEP_DEG,
    HUMAN_RADIUS_M,
    OBSTACLE_IGNORE_NAMES,
    OBSTACLE_MIN_HEIGHT_M,
    OBSTACLE_PATH_PREFIX,
)
from smart_home.episode_logging import EpisodeJsonlLogger, utc_now_iso
from smart_home.live.media import rgb_obs_to_jpeg, write_rgb_obs_jpeg, zero_action_like
from smart_home.replay import ReplayRegistry, ReplaySelectionError
from smart_home.scene_profile import load_scene_profile
from smart_home.sensors import SmartHomeSensorRig
from smart_home.service_types import SmartHomeState, TaskCommand


SCENARIO_REPLAY_RULES = {
    "arriving_home": "delivery_med_room_2_sc",
    "watching_tv": "delivery_med_room_2_sc",
    "resting_on_sofa": "delivery_med_room_2_sc",
    "playing_piano": "delivery_med_room_1_v01_repaired_v09",
    "lying_in_bed": "delivery_med_room_3",
    "toilet_use": "delivery_failure_case",
}

TASK_EVAL_OBJECT_NAME = "medicine_bottle_0"
TASK_EVAL_CAP_OFFSET_M = 0.08
TASK_EVAL_GRIPPER_RADIUS_M = 0.18
TASK_EVAL_GRASP_HOLD_S = 0.50
TASK_EVAL_MOVE_SPEED_THRESHOLD_MPS = 0.025
TASK_EVAL_MOVE_HOLD_S = 0.80
TASK_EVAL_PLACE_HOLD_S = 1.20
TASK_EVAL_TARGET_HALF_EXTENTS = [0.65, 0.65, 0.75]
TASK_EVAL_WEIGHTS = {
    "grasp": 0.30,
    "transport": 0.30,
    "place": 0.40,
}
ASSET_RESET_EXCLUDED_PREFIXES = (
    "robot",
    "walls_",
    "floors_",
    "ceilings_",
    "scene_",
    "smart_home",
    "physics",
)


def configured_scene_file_for(repo_root, scene_profile, *, allow_doorless):
    if scene_profile is None:
        return None, "default"
    raw_path = scene_profile.scene_file
    variant = "profile_main"
    if not raw_path and allow_doorless and scene_profile.doorless_scene_file:
        raw_path = scene_profile.doorless_scene_file
        variant = "doorless_main"
    if not raw_path:
        return None, "default"
    path = Path(raw_path)
    return (path if path.is_absolute() else repo_root / path), variant


class LiveControlledScene:
    def __init__(self, args):
        self.args = args
        self.state = SmartHomeState()
        self.registry = ReplayRegistry.from_json(REPO_ROOT / "smart_home" / "configs" / "replay_registry.json")
        self.robot_task_end_t = None
        self.dummy_root = None
        self.sensor_rig = None
        self.current_readings = {}
        self.command_queue = queue.Queue()
        self.pending_robot_task = False
        self.video_source = "viewer"
        self.video_frame_interval_s = 1.0 / max(float(getattr(args, "video_fps", 10.0)), 1.0)
        self.last_video_frame_t = 0.0
        self.camera_log_enabled = bool(getattr(args, "save_camera_frames", False))
        self.camera_log_interval_s = 1.0 / max(float(getattr(args, "camera_log_fps", 2.0)), 0.001)
        self.last_camera_log_t = 0.0
        self.camera_frame_seq = 0
        self.camera_log_sources = self.parse_camera_log_sources(getattr(args, "camera_log_sources", "robot"))
        self.camera_frame_counts = {source: 0 for source in ("top", "robot")}
        self.camera_frame_missing_counts = {source: 0 for source in ("top", "robot")}
        self.follow_camera_interval_s = 0.10
        self.last_follow_camera_t = 0.0
        self.default_viewer_camera_pose = None
        self.viewer_camera_mover = None
        self.robot_rgb_sensor = None
        self.robot_rgb_sensors = {}
        self.ceiling_prims = []
        self.ceiling_hidden = False
        self.ceiling_visibility_dirty = False
        self.human_target_pos = None
        self.human_last_move_t = time()
        self.human_heading_deg = 0.0
        self.human_input_vector = (0.0, 0.0, 0.0)
        self.scene_bounds = None
        self.collision_obstacles = []
        self.sensor_ranges_visible = False
        self.sensor_visual_flush_frames = 0
        self.sensor_layout = str(getattr(args, "sensor_layout", "current") or "current")
        self.viewport_camera_modes = ("overview", "resident", "free")
        self.viewport_input_active = False
        self.viewport_input_expires_t = 0.0
        self.viewport_input_vector = (0.0, 0.0, 0.0)
        self.viewport_input_face_movement = True
        self.scene_profile = None
        self.pressure_sensor_name = "pressure_sensor_0"
        self.rng = random.Random(args.episode_seed)
        episode_log_dir = Path(args.episode_log_dir)
        if not episode_log_dir.is_absolute():
            episode_log_dir = REPO_ROOT.parent / episode_log_dir
        self.episode_logger = EpisodeJsonlLogger(episode_log_dir, enabled=not args.disable_episode_logging)
        self.episode_id = 0
        self.episode_started_at = None
        self.episode_seed = args.episode_seed
        self.episode_zone = None
        self.episode_scenario_type = None
        self.episode_metrics = self.empty_episode_metrics()
        self.task_eval = self.empty_task_eval()
        self.task_eval_last_update_t = None
        self.task_eval_last_object_pos = None
        self.activity_simulator = ActivitySensorSimulator(enabled=False)
        self.activity_state = ActivityState()
        self.human_posture = "standing"
        self.resident_context = {}
        self.step_log_interval_s = 1.0 / max(float(args.step_log_hz), 0.001) if args.step_log_hz > 0 else None
        self.last_step_log_t = 0.0
        self.hud_window = None
        self.hud_labels = {}
        self.last_hud_update_t = 0.0
        self.hdf5_replay_actions = None
        self.hdf5_replay_states = None
        self.hdf5_replay_state_sizes = None
        self.hdf5_replay_config = None
        self.hdf5_replay_id = None
        self.hdf5_replay_scene_file_path = None
        self.hdf5_replay_paths = []
        self.hdf5_replay_index = -1
        self.hdf5_replay_playback_mode = "action"
        self.hdf5_state_replay_failed = False
        self.active_scene_file = None
        self.active_scene_variant = "default"
        self.robot_replay_active = False
        self.robot_replay_paused = False
        self.robot_replay_step = 0
        self.last_robot_action_record = {
            "source": "zero",
            "step": None,
            "vector": None,
            "controller": None,
            "normalized": None,
        }

    @staticmethod
    def parse_camera_log_sources(value):
        value = str(value or "robot").strip().lower()
        if value == "all":
            return ("top", "robot")
        sources = []
        for item in value.split(","):
            item = item.strip()
            if item in {"top", "robot"} and item not in sources:
                sources.append(item)
        return tuple(sources or ["robot"])

    @staticmethod
    def safe_camera_name(value, fallback="camera"):
        name = str(value or fallback).strip()
        name = name.split("/")[-1]
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._-")
        return name or fallback

    @staticmethod
    def replay_key_for_path(path):
        stem = Path(path).stem
        if stem.endswith("_v01"):
            return stem[:-4]
        return stem

    @staticmethod
    def should_restore_asset_state(name, state):
        name = str(name)
        if name.startswith(ASSET_RESET_EXCLUDED_PREFIXES):
            return False
        root_link = (state or {}).get("root_link") or {}
        return root_link.get("pos") is not None and root_link.get("ori") is not None

    def discover_hdf5_replay_paths(self):
        candidates = []
        if self.args.hdf5_replay:
            candidates.append(Path(self.args.hdf5_replay))
        for raw_dir in getattr(self.args, "hdf5_replay_dir", []) or []:
            replay_dir = Path(raw_dir)
            if not replay_dir.is_absolute():
                replay_dir = REPO_ROOT / replay_dir
            candidates.extend(sorted(replay_dir.glob("*.hdf5")))
        candidates.extend(sorted((REPO_ROOT.parent).glob("delivery_med_room_*.hdf5")))
        candidates.extend(sorted((REPO_ROOT.parent).glob("delivery_failure_case*.hdf5")))
        candidates.extend(sorted((REPO_ROOT.parent / "replay-data").glob("*.hdf5")))

        paths = []
        seen = set()
        for path in candidates:
            if not path.is_absolute():
                path = REPO_ROOT / path
            try:
                resolved = path.resolve()
            except FileNotFoundError:
                continue
            if not resolved.exists() or resolved in seen:
                continue
            # Keep automatic demo selection scoped to scenario-mapped replays.
            if resolved.stem not in set(SCENARIO_REPLAY_RULES.values()):
                continue
            seen.add(resolved)
            paths.append(resolved)
        return paths

    def setup(self):
        preset = PRESETS[self.args.preset]
        scene_model = self.args.scene_model or preset["scene"]
        self.scene_profile = load_scene_profile(REPO_ROOT, scene_model)
        self.load_hdf5_replay()
        if self.scene_profile is None:
            bridge.log(
                "scene_profile_missing",
                {
                    "scene_model": scene_model,
                    "reason": "using preset/default sensor fallback; add smart_home/configs/scenes/<scene>.yaml for full scene-specific behavior",
                },
            )
        else:
            bridge.log(
                "scene_profile_loaded",
                {
                    "scene_model": scene_model,
                    "zones": sorted(self.scene_profile.zones),
                    "motion_sensors": len(self.scene_profile.motion_sensors),
                    "activity_profiles": sum(len(profiles) for profiles in self.scene_profile.activity_profiles.values()),
                    "doorless_portals": len(self.scene_profile.doorless_portals),
                },
            )
        self.activity_simulator = ActivitySensorSimulator(
            profiles=self.scene_profile.activity_profiles if self.scene_profile is not None else {},
            enabled=self.args.enable_activity_sensors,
        )
        gm.HEADLESS = False
        gm.USE_GPU_DYNAMICS = not self.args.cpu_dynamics
        selected_scene_file, selected_scene_variant = configured_scene_file_for(
            REPO_ROOT,
            self.scene_profile,
            allow_doorless=bool(self.args.doorless_scene),
        )
        replay_scene_source = str(getattr(self.args, "hdf5_replay_scene_source", "auto") or "auto")
        if (
            self.hdf5_replay_scene_file_path is not None
            and replay_scene_source in {"auto", "hdf5"}
            and self.hdf5_replay_playback_mode == "state"
        ):
            selected_scene_file = self.hdf5_replay_scene_file_path
            selected_scene_variant = "hdf5_embedded"
        self.active_scene_file = selected_scene_file
        self.active_scene_variant = selected_scene_variant
        using_doorless_scene = selected_scene_variant == "doorless_main"
        gm.ENABLE_FLATCACHE = self.args.flatcache if self.args.flatcache is not None else not using_doorless_scene
        gm.ENABLE_OBJECT_STATES = False
        gm.ENABLE_TRANSITION_RULES = False
        bridge.log(
            "simulator_cache_mode",
            {
                "flatcache": bool(gm.ENABLE_FLATCACHE),
                "reason": "doorless Merom disables flatcache to avoid PhysX/Fabric flush crashes"
                if using_doorless_scene and not gm.ENABLE_FLATCACHE
                else "default",
            },
        )

        if self.args.empty_scene:
            scene_cfg = {"type": "Scene"}
        else:
            scene_cfg = {
                "type": "InteractiveTraversableScene",
                "scene_model": scene_model,
                "load_object_categories": list(STRUCTURE_CATEGORIES) if not self.args.full else None,
                "include_robots": True,
            }
            if selected_scene_file is not None:
                if not selected_scene_file.exists():
                    raise FileNotFoundError(
                        f"Configured scene file is missing: {selected_scene_file}. "
                        "Sync the tracked scene JSON before launching, or disable the scene override."
                    )
                scene_cfg["scene_file"] = str(selected_scene_file)
                bridge.log(
                    "scene_file_selected",
                    {
                        "scene_model": scene_model,
                        "scene_file": str(selected_scene_file),
                        "scene_variant": selected_scene_variant,
                    },
                )
        robot_cfg = {
            "type": self.args.robot_type,
            "obs_modalities": ["rgb"],
            "action_type": "continuous",
            "action_normalize": True,
            "scale": 1.0,
            "self_collision": False,
        }
        if self.hdf5_replay_config is not None:
            replay_robot_cfg = self.hdf5_replay_config.get("robot_config") or {}
            robot_cfg["action_normalize"] = bool(replay_robot_cfg.get("action_normalize", robot_cfg["action_normalize"]))
            if "controller_config" in replay_robot_cfg:
                robot_cfg["controller_config"] = replay_robot_cfg["controller_config"]
            if "reset_joint_pos" in replay_robot_cfg:
                robot_cfg["reset_joint_pos"] = replay_robot_cfg["reset_joint_pos"]
            if "grasping_mode" in replay_robot_cfg:
                robot_cfg["grasping_mode"] = replay_robot_cfg["grasping_mode"]
            if "sensor_config" in replay_robot_cfg:
                robot_cfg["sensor_config"] = replay_robot_cfg["sensor_config"]
            if "self_collisions" in replay_robot_cfg:
                robot_cfg["self_collision"] = bool(replay_robot_cfg["self_collisions"])
            if "position" in replay_robot_cfg:
                robot_cfg["position"] = replay_robot_cfg["position"]
            if "orientation" in replay_robot_cfg:
                robot_cfg["orientation"] = replay_robot_cfg["orientation"]
            robot_registry_name = self.hdf5_replay_config.get("robot_registry_name")
            if robot_registry_name:
                robot_cfg["name"] = robot_registry_name
            bridge.log(
                "hdf5_replay_robot_config_applied",
                {
                    "replay_id": self.hdf5_replay_id,
                    "robot_name": robot_cfg.get("name"),
                    "action_normalize": robot_cfg.get("action_normalize"),
                    "controller_groups": sorted((robot_cfg.get("controller_config") or {}).keys()),
                    "has_reset_joint_pos": "reset_joint_pos" in robot_cfg,
                },
            )

        cfg = {
            "scene": scene_cfg,
            "robots": [robot_cfg],
            "task": {"type": "DummyTask"},
        }
        if self.args.full and not self.args.empty_scene:
            cfg["scene"].pop("load_object_categories", None)

        self.env = og.Environment(configs=cfg)
        self.robot = self.env.robots[0] if self.env.robots else None
        self.capture_default_viewer_camera_pose()
        self.apply_robot_initial_pose()
        self.add_scene_profile_objects()
        self.sync_hdf5_replay_object_poses("scene_initialization")
        self.sync_hdf5_replay_robot_state("scene_initialization")
        self.robot_rgb_sensors = self._find_robot_rgb_sensors()
        self.robot_rgb_sensor = next(iter(self.robot_rgb_sensors.values()), None)
        bridge.log(
            "robot_rgb_sensors_discovered",
            {
                "count": len(self.robot_rgb_sensors),
                "names": list(self.robot_rgb_sensors),
                "primary": next(iter(self.robot_rgb_sensors), None),
            },
        )
        self.zero_action = zero_action_like(self.env.action_space.sample()) if self.env.action_space is not None else []
        bridge.log("robot_initial_pose", self.current_robot_pose() or {"available": False})

        with og.sim.editing_usd():
            if self.args.clean_structure_materials and not self.args.empty_scene:
                _clean_structure_materials()
            if self.args.empty_scene:
                self._add_empty_scene_floor()

            if self.args.open_doors and not self.args.empty_scene:
                bridge.log(
                    "runtime_door_hiding_skipped",
                    {
                        "reason": "Door prim visibility edits can crash PhysX/Fabric; use the doorless scene JSON instead.",
                    },
                )
            if self.args.doorless_scene and using_doorless_scene:
                self.ensure_doorless_collision_groups()
            self.cache_scene_geometry()
            self.cache_ceiling_prims()
            self.cache_collision_obstacles()
            human_start_pos = self.human_start_position(preset)
            self.dummy_root, human_visual_asset = add_demo_human_avatar(
                position=human_start_pos,
                height=1.7,
                collision_enabled=self.args.human_collision_mode == "solid",
                show_collision_proxy=self.args.show_human_collision_proxy,
                visual_mode=self.args.human_visual_mode,
            )
            bridge.log(
                "human_avatar_configured",
                {
                    "visual_asset": human_visual_asset,
                    "visual_mode": self.args.human_visual_mode,
                    "mode": self.args.human_collision_mode,
                    "visible": bool(self.args.show_human_collision_proxy),
                    "radius_m": 0.27,
                },
            )
            self.human_target_pos = self.find_nearest_free_position(human_start_pos)
            self._set_human_pose(self.human_target_pos, self.human_heading_deg)
            self.sensor_rig = SmartHomeSensorRig(
                motion_position=preset["motion_pos"],
                motion_yaw_deg=preset["motion_yaw_deg"],
                motion_range_m=preset.get("motion_range", 2.5),
                motion_fov_deg=preset.get("motion_fov_deg", 60.0),
                motion_sensors=self.motion_sensor_specs(),
                show_motion_fov=False,
                pressure_position=self.pressure_sensor_position(preset),
                pressure_name=self.pressure_sensor_name,
                pressure_size=self.pressure_sensor_size(),
                pressure_threshold_kg=self.pressure_sensor_threshold_kg(),
                show_pressure_visual=False,
            )
            self.sensor_rig.set_motion_occluders(self.sensor_wall_occluders())
            bridge.log("sensor_layout_selected", self.apply_sensor_layout(self.sensor_layout))
            bridge.log("pressure_sensor_visual_disabled", {"reason": "avoid overlapping Merom laundry geometry"})
        self.set_camera("overview")
        self.attach_bridge()
        self.configure_keyboard()
        self.create_viewport_hud()
        self.write_dataset_manifest(scene_model)
        self.start_episode(zone=self.args.resident_zone, reason="startup", randomize=False)

    def human_start_position(self, preset):
        if self.args.empty_scene or self.scene_profile is None:
            return preset["dummy_pos"]
        return self.scene_profile.human_start_pos

    def motion_sensor_specs(self):
        if self.args.empty_scene or self.scene_profile is None or not self.scene_profile.motion_sensors:
            return None
        specs_by_name = {}
        for specs in self.available_sensor_layouts().values():
            for spec in specs:
                specs_by_name.setdefault(str(spec["name"]), dict(spec))
        if not specs_by_name:
            for spec in self.scene_profile.motion_sensors:
                specs_by_name.setdefault(str(spec["name"]), dict(spec))
        return list(specs_by_name.values())

    def available_sensor_layouts(self):
        if self.args.empty_scene or self.scene_profile is None:
            return {}
        layouts = {
            str(name): tuple(dict(spec) for spec in specs)
            for name, specs in (self.scene_profile.sensor_layouts or {}).items()
        }
        if "current" not in layouts and self.scene_profile.motion_sensors:
            layouts["current"] = tuple(dict(spec) for spec in self.scene_profile.motion_sensors)
        return layouts

    def ordered_sensor_layout_names(self):
        layouts = self.available_sensor_layouts()
        preferred = [name for name in ("current", "dense", "sparse") if name in layouts]
        return preferred + sorted(name for name in layouts if name not in preferred)

    def active_sensor_names_for_layout(self, layout_name):
        layouts = self.available_sensor_layouts()
        selected = str(layout_name or "current")
        if selected not in layouts:
            fallback = "current" if "current" in layouts else next(iter(layouts), None)
            if fallback is not None:
                bridge.log("sensor_layout_fallback", {"requested": selected, "selected": fallback})
                selected = fallback
        specs = layouts.get(selected, ())
        return selected, [str(spec["name"]) for spec in specs]

    def apply_sensor_layout(self, layout_name):
        selected, sensor_names = self.active_sensor_names_for_layout(layout_name)
        self.sensor_layout = selected
        active = []
        if self.sensor_rig is not None:
            active = self.sensor_rig.set_active_motion_sensors(sensor_names)
            self.sensor_rig.set_motion_fov_visible(self.sensor_ranges_visible)
        return {
            "event": "sensor_layout_selected",
            "sensor_layout": self.sensor_layout,
            "active_motion_sensor_count": len(active),
            "active_motion_sensors": active,
            "available_sensor_layouts": self.ordered_sensor_layout_names(),
        }

    def cycle_sensor_layout(self):
        names = self.ordered_sensor_layout_names()
        if not names:
            return {"event": "sensor_layout_unavailable"}
        try:
            idx = names.index(self.sensor_layout)
        except ValueError:
            idx = -1
        result = self.apply_sensor_layout(names[(idx + 1) % len(names)])
        if self.episode_id > 0:
            self.episode_logger.write("sensor_layout_changed", self.episode_payload(event_reason=f"sensor_layout_{self.sensor_layout}"))
        return result

    def pressure_sensor_position(self, preset):
        sensor = None if self.scene_profile is None else self.scene_profile.primary_pressure_sensor
        if sensor is not None:
            self.pressure_sensor_name = str(sensor.get("name", "pressure_sensor_0"))
        return sensor["position"] if sensor is not None else preset["pressure_pos"]

    def pressure_sensor_size(self):
        sensor = None if self.scene_profile is None else self.scene_profile.primary_pressure_sensor
        return tuple(sensor.get("size", [0.9, 0.9, 0.03])) if sensor is not None else (0.9, 0.9, 0.03)

    def pressure_sensor_threshold_kg(self):
        sensor = None if self.scene_profile is None else self.scene_profile.primary_pressure_sensor
        return float(sensor.get("threshold_kg", 6.0)) if sensor is not None else 6.0

    def empty_episode_metrics(self):
        return {
            "min_robot_resident_distance_m": None,
            "frame_count": 0,
            "camera_sample_count": 0,
            "camera_frame_counts": {"top": 0, "robot": 0},
            "camera_frame_missing_counts": {"top": 0, "robot": 0},
            "task_started": False,
            "task_completed": False,
            "task_blocked": False,
            "last_task": None,
            "last_replay_id": None,
        }

    def reset_task_run_counters(self):
        self.episode_metrics = self.empty_episode_metrics()
        self.camera_frame_counts = {source: 0 for source in ("top", "robot")}
        self.camera_frame_missing_counts = {source: 0 for source in ("top", "robot")}
        self.camera_frame_seq = 0
        self.last_camera_log_t = 0.0

    def empty_task_eval(self):
        return {
            "schema_version": "homesense_task_eval_v1",
            "enabled": False,
            "finalized": False,
            "task": None,
            "replay_id": None,
            "object_name": TASK_EVAL_OBJECT_NAME,
            "target": {
                "source": None,
                "center": None,
                "half_extents": list(TASK_EVAL_TARGET_HALF_EXTENTS),
            },
            "thresholds": {
                "cap_offset_m": TASK_EVAL_CAP_OFFSET_M,
                "gripper_radius_m": TASK_EVAL_GRIPPER_RADIUS_M,
                "grasp_hold_s": TASK_EVAL_GRASP_HOLD_S,
                "move_speed_threshold_mps": TASK_EVAL_MOVE_SPEED_THRESHOLD_MPS,
                "move_hold_s": TASK_EVAL_MOVE_HOLD_S,
                "place_hold_s": TASK_EVAL_PLACE_HOLD_S,
            },
            "weights": dict(TASK_EVAL_WEIGHTS),
            "subgoals": {
                "grasp": {"success": False, "hold_s": 0.0, "contact": False, "min_cap_distance_m": None},
                "transport": {"success": False, "moving_streak_s": 0.0, "max_speed_mps": 0.0},
                "place": {"success": False, "hold_s": 0.0, "inside_target": False},
            },
            "object": {
                "position": None,
                "initial_position": None,
                "cap_point": None,
                "speed_mps": 0.0,
            },
            "score": 0.0,
            "success": False,
            "label": "not_started",
            "reason": None,
            "started_at_wall_time_s": None,
            "updated_at_wall_time_s": None,
            "finished_at_wall_time_s": None,
        }

    def write_dataset_manifest(self, scene_model):
        self.episode_logger.write_manifest(
            {
                "schema_version": "homesense_dataset_session_v1",
                "gym_framework": self.gym_framework_metadata(
                    task="deliver_item",
                    replay_id=self.hdf5_replay_id,
                    policy_source="teleoperation_replay" if self.hdf5_replay_actions is not None else "manual_or_scripted",
                ),
                "quality_schema_version": "homesense_quality_v1",
                "dataset_type": "multimodal_smart_home_robot_task_runs",
                "dataset_unit": "task_replay_run",
                "scene_model": scene_model,
                "scene_file": str(self.active_scene_file) if self.active_scene_file is not None else None,
                "scene_variant": self.active_scene_variant,
                "robot_type": self.args.robot_type,
                "resident_zone_mode": self.args.resident_zone,
                "episode_seed": self.episode_seed,
                "activity_sensors_enabled": bool(self.args.enable_activity_sensors),
                "initial_sensor_layout": self.sensor_layout,
                "available_sensor_layouts": self.ordered_sensor_layout_names(),
                "step_log_hz": float(self.args.step_log_hz),
                "camera_logging": {
                    "enabled": bool(self.camera_log_enabled),
                    "sources": list(self.camera_log_sources),
                    "robot_camera_mode": "all_rgb_sensors",
                    "robot_camera_names": list(self.robot_rgb_sensors),
                    "fps": float(self.args.camera_log_fps),
                    "width": int(self.args.camera_log_width),
                    "quality": int(self.args.camera_log_quality),
                    "top_source_note": "top frames use the current viewer camera only while viewport mode is overview",
                },
                "data_modalities": [
                    "resident_state",
                    "smart_home_sensors",
                    "virtual_sensors",
                    "robot_state",
                    "robot_action",
                    "object_state",
                    "safety",
                    "camera_frame_reference",
                ],
                "training_scope": {
                    "context_model": True,
                    "task_selection": True,
                    "safety_eval": True,
                    "policy_behavior_cloning": bool(self.args.hdf5_replay),
                    "human_aware_planning": "future_extension",
                },
                "files": {
                    "events": "metadata/events.jsonl",
                    "steps": "data/steps.jsonl",
                    "manifest": "metadata/manifest.json",
                    "metadata": "metadata/metadata.json",
                    "annotations": "metadata/annotations.json",
                    "quality_report": "metadata/quality_report.json",
                    "hdf5": "metadata/dataset.hdf5",
                    "camera_dir": "data/cameras/<robot_camera_name>/",
                    "legacy_event_index": None
                    if self.episode_logger.path is None
                    else str(self.episode_logger.path.relative_to(self.episode_logger.log_dir)),
                },
            }
        )

    def load_hdf5_replay(self, replay_path=None, reason="startup"):
        import h5py

        if replay_path is None:
            self.hdf5_replay_paths = self.discover_hdf5_replay_paths()
            if not self.hdf5_replay_paths:
                return
            replay_path = self.hdf5_replay_paths[0]
        replay_path = Path(replay_path)
        if not replay_path.is_absolute():
            replay_path = REPO_ROOT / replay_path
        if not replay_path.exists():
            raise FileNotFoundError(f"HDF5 replay file does not exist: {replay_path}")
        replay_path = replay_path.resolve()
        self.hdf5_replay_actions = None
        self.hdf5_replay_states = None
        self.hdf5_replay_state_sizes = None
        self.hdf5_replay_config = None
        self.hdf5_replay_scene_file_path = None
        self.hdf5_state_replay_failed = False
        self.robot_replay_paused = False
        self.hdf5_replay_id = replay_path.stem
        if replay_path in self.hdf5_replay_paths:
            self.hdf5_replay_index = self.hdf5_replay_paths.index(replay_path)
        with h5py.File(replay_path, "r") as f:
            data = f["data"]
            demo_key = f"demo_{self.args.hdf5_replay_episode}"
            if demo_key not in data:
                raise ValueError(f"HDF5 replay has no {demo_key}; available demos: {sorted(k for k in data if k.startswith('demo_'))}")
            config = json.loads(data.attrs["config"])
            scene_file = json.loads(data.attrs["scene_file"])
            demo = data[demo_key]
            self.hdf5_replay_actions = th.tensor(demo["action"][:], dtype=th.float32)
            if "state" in demo and "state_size" in demo:
                self.hdf5_replay_states = th.tensor(demo["state"][:], dtype=th.float32)
                self.hdf5_replay_state_sizes = th.tensor(demo["state_size"][:], dtype=th.int64)
            self.hdf5_replay_config = {
                "path": str(replay_path),
                "replay_key": self.replay_key_for_path(replay_path),
                "demo_key": demo_key,
                "scene_model": (config.get("scene") or {}).get("scene_model"),
                "scene_file": (config.get("scene") or {}).get("scene_file"),
                "embedded_scene_file": None,
                "robot_config": dict((config.get("robots") or [{}])[0]),
                "scene_metadata": scene_file.get("metadata", {}),
                "object_poses": {},
                "object_states": {},
                "robot_state": {},
                "robot_registry_name": None,
            }
            object_registry = ((scene_file.get("state") or {}).get("registry") or {}).get("object_registry") or {}
            if scene_file:
                replay_scene_dir = REPO_ROOT / "logs" / "homesense_replay_scenes"
                replay_scene_dir.mkdir(parents=True, exist_ok=True)
                scene_path = replay_scene_dir / f"{self.hdf5_replay_id}_{demo_key}.json"
                scene_path.write_text(json.dumps(scene_file, indent=2), encoding="utf-8")
                self.hdf5_replay_scene_file_path = scene_path
                self.hdf5_replay_config["embedded_scene_file"] = str(scene_path)
            robot_registry_names = [name for name in object_registry if str(name).startswith("robot")]
            if robot_registry_names:
                self.hdf5_replay_config["robot_registry_name"] = sorted(
                    robot_registry_names,
                    key=lambda name: (name != "robot", name),
                )[0]
            for object_name, object_state in object_registry.items():
                if not self.should_restore_asset_state(object_name, object_state):
                    continue
                root_link = (object_state or {}).get("root_link") or {}
                if root_link.get("pos") is not None and root_link.get("ori") is not None:
                    self.hdf5_replay_config["object_poses"][object_name] = {
                        "position": root_link["pos"],
                        "orientation_xyzw": root_link["ori"],
                    }
                    self.hdf5_replay_config["object_states"][object_name] = dict(object_state or {})
            robot_state = dict(object_registry.get("robot") or {})
            root_link = robot_state.get("root_link") or {}
            if root_link.get("pos") is not None and root_link.get("ori") is not None:
                self.hdf5_replay_config["robot_state"] = robot_state
        requested_mode = str(getattr(self.args, "hdf5_replay_playback", "auto") or "auto")
        controller_groups = set((self.hdf5_replay_config["robot_config"].get("controller_config") or {}).keys())
        has_base_controller = bool(controller_groups & {"base", "base_drive", "locomotion"})
        has_state_replay = self.hdf5_replay_states is not None and self.hdf5_replay_state_sizes is not None
        if requested_mode == "state":
            self.hdf5_replay_playback_mode = "state" if has_state_replay else "action"
        elif requested_mode == "action":
            self.hdf5_replay_playback_mode = "action"
        else:
            self.hdf5_replay_playback_mode = "state" if has_state_replay and not has_base_controller else "action"
        bridge.log(
            "hdf5_replay_loaded",
            {
                "path": str(replay_path),
                "reason": reason,
                "demo_key": self.hdf5_replay_config["demo_key"],
                "replay_key": self.hdf5_replay_config["replay_key"],
                "available_replays": [path.stem for path in self.hdf5_replay_paths],
                "scene_model": self.hdf5_replay_config["scene_model"],
                "num_actions": int(self.hdf5_replay_actions.shape[0]),
                "action_dim": int(self.hdf5_replay_actions.shape[1]) if self.hdf5_replay_actions.ndim > 1 else None,
                "action_normalize": self.hdf5_replay_config["robot_config"].get("action_normalize"),
                "playback_mode": self.hdf5_replay_playback_mode,
                "has_state_replay": has_state_replay,
                "has_base_controller": has_base_controller,
                "embedded_scene_file": self.hdf5_replay_config["embedded_scene_file"],
                "robot_registry_name": self.hdf5_replay_config["robot_registry_name"],
                "restorable_object_count": len(self.hdf5_replay_config["object_states"]),
                "restorable_objects": sorted(self.hdf5_replay_config["object_states"])[:32],
                "object_poses": {
                    name: pose
                    for name, pose in self.hdf5_replay_config["object_poses"].items()
                    if name == TASK_EVAL_OBJECT_NAME
                },
            },
        )


    def _add_empty_scene_floor(self):
        pxr = lazy.pxr
        stage = og.sim.stage
        floor = pxr.UsdGeom.Cube.Define(stage, "/World/smart_home_floor")
        floor.CreateSizeAttr(1.0)
        floor.AddTranslateOp().Set(pxr.Gf.Vec3d(0.0, 0.0, -0.03))
        floor.AddScaleOp().Set(pxr.Gf.Vec3d(5.0, 5.0, 0.03))
        floor.CreateDisplayColorAttr([pxr.Gf.Vec3f(0.55, 0.57, 0.58)])
        if not floor.GetPrim().HasAPI(pxr.UsdPhysics.CollisionAPI):
            pxr.UsdPhysics.CollisionAPI.Apply(floor.GetPrim())

    def _bbox_for_prim(self, prim):
        pxr = lazy.pxr
        purposes = [pxr.UsdGeom.Tokens.default_, pxr.UsdGeom.Tokens.render, pxr.UsdGeom.Tokens.proxy]
        cache = pxr.UsdGeom.BBoxCache(pxr.Usd.TimeCode.Default(), purposes, useExtentsHint=True)
        box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        min_pt = box.GetMin()
        max_pt = box.GetMax()
        if not all(math.isfinite(float(v)) for v in [min_pt[0], min_pt[1], min_pt[2], max_pt[0], max_pt[1], max_pt[2]]):
            return None
        return (
            [float(min_pt[0]), float(min_pt[1]), float(min_pt[2])],
            [float(max_pt[0]), float(max_pt[1]), float(max_pt[2])],
        )

    def cache_scene_geometry(self):
        if self.args.empty_scene:
            return
        root = og.sim.stage.GetPrimAtPath(OBSTACLE_PATH_PREFIX.rstrip("/"))
        if not root or not root.IsValid():
            return
        bbox = self._bbox_for_prim(root)
        if bbox is None:
            return
        min_pt, max_pt = bbox
        self.scene_bounds = {
            "min": min_pt,
            "max": max_pt,
            "center": [(min_pt[0] + max_pt[0]) * 0.5, (min_pt[1] + max_pt[1]) * 0.5, 0.0],
            "size": [max_pt[0] - min_pt[0], max_pt[1] - min_pt[1], max_pt[2] - min_pt[2]],
        }
        bridge.log("scene_bounds_cached", self.scene_bounds)

    def cache_collision_obstacles(self):
        if self.args.empty_scene:
            self.collision_obstacles = []
            return
        obstacles = []
        scene_root = og.sim.stage.GetPrimAtPath(OBSTACLE_PATH_PREFIX.rstrip("/"))
        if not scene_root or not scene_root.IsValid():
            return
        pxr = lazy.pxr
        for prim in og.sim.stage.Traverse():
            if not prim.IsValid():
                continue
            path = str(prim.GetPath())
            if not path.startswith(OBSTACLE_PATH_PREFIX):
                continue
            name = prim.GetName().lower()
            path_l = path.lower()
            if any(token in name or token in path_l for token in OBSTACLE_IGNORE_NAMES):
                continue
            if not prim.IsA(pxr.UsdGeom.Boundable):
                continue
            bbox = self._bbox_for_prim(prim)
            if bbox is None:
                continue
            min_pt, max_pt = bbox
            height = max_pt[2] - min_pt[2]
            width = max_pt[0] - min_pt[0]
            depth = max_pt[1] - min_pt[1]
            if height < OBSTACLE_MIN_HEIGHT_M or width < 0.04 or depth < 0.04:
                continue
            obstacles.append({"path": path, "min": min_pt, "max": max_pt})
        self.collision_obstacles = obstacles
        bridge.log("collision_obstacles_cached", {"count": len(obstacles), "mode": "boundable_leaf_prims"})

    def sensor_wall_occluders(self):
        occluders = []
        for obstacle in self.collision_obstacles:
            path = obstacle["path"].lower()
            if "wall" not in path:
                continue
            occluders.append(obstacle)
        bridge.log("sensor_occluders_cached", {"count": len(occluders), "mode": "wall_bbox_line_of_sight"})
        return occluders

    def open_doors_for_demo(self):
        pxr = lazy.pxr
        # This is a demo shortcut, not physical handle/latch simulation. Merom's
        # door object names are fixed by the scene JSON; use the exact list so we
        # do not accidentally edit OmniGraph / Fabric internals at runtime.
        changed_paths = []
        missing = []
        door_names = self.scene_profile.door_object_names if self.scene_profile is not None else ()
        for name in door_names:
            prim = og.sim.stage.GetPrimAtPath(f"{OBSTACLE_PATH_PREFIX}{name}")
            if not prim or not prim.IsValid():
                missing.append(name)
                continue
            try:
                pxr.UsdGeom.Imageable(prim).MakeInvisible()
            except Exception:
                pass
            changed_paths.append(str(prim.GetPath()))

        bridge.log(
            "doors_opened_for_demo",
            {
                "count": len(changed_paths),
                "paths": changed_paths[:30],
                "missing": missing,
                "mode": "top_level_hidden_resident_collision_ignored",
            },
        )

    def ensure_doorless_collision_groups(self):
        pxr = lazy.pxr
        stage = og.sim.stage
        door_group_path = "/World/collision_groups/structural_doors"
        fixed_group_path = "/World/collision_groups/fixed_base_fixed_links"
        if not stage.GetPrimAtPath("/World/collision_groups").IsValid():
            stage.DefinePrim("/World/collision_groups", "Scope")
        door_group = pxr.UsdPhysics.CollisionGroup.Define(stage, door_group_path)
        fixed_group = pxr.UsdPhysics.CollisionGroup.Define(stage, fixed_group_path)
        door_group.GetFilteredGroupsRel().AddTarget(pxr.Sdf.Path(door_group_path))
        door_group.GetFilteredGroupsRel().AddTarget(pxr.Sdf.Path(fixed_group_path))
        fixed_group.GetFilteredGroupsRel()
        bridge.log(
            "doorless_collision_groups_repaired",
            {
                "door_group": door_group_path,
                "fixed_group": fixed_group_path,
                "reason": "Door objects are removed, but PhysX/Fabric still expects the structural door collision group.",
            },
        )

    def cache_ceiling_prims(self):
        if self.args.empty_scene or self.args.preserve_ceiling:
            self.ceiling_prims = []
            return
        ceiling_ids = set(CEILING_MODEL_IDS)
        if self.scene_profile is not None and self.scene_profile.ceiling_model_ids:
            ceiling_ids = set(self.scene_profile.ceiling_model_ids)
        candidates = []
        for prim in og.sim.stage.Traverse():
            if not prim.IsValid():
                continue
            path = str(prim.GetPath()).lower()
            name = prim.GetName().lower()
            if "ceiling" in path or "ceilings" in path or name in ceiling_ids:
                candidates.append(prim)
        candidate_paths = {str(prim.GetPath()) for prim in candidates}
        self.ceiling_prims = [
            prim
            for prim in candidates
            if not any(
                str(prim.GetPath()) != other and str(prim.GetPath()).startswith(f"{other}/")
                for other in candidate_paths
            )
        ]
        bridge.log(
            "ceiling_prims_cached",
            {"count": len(self.ceiling_prims), "paths": [str(prim.GetPath()) for prim in self.ceiling_prims[:20]]},
        )

    def set_ceiling_visibility(self, visible):
        if self.args.preserve_ceiling or not self.ceiling_prims:
            return {"event": "ceiling_visibility_skipped", "visible": visible, "count": len(self.ceiling_prims)}
        if self.ceiling_hidden == (not visible):
            return {"event": "ceiling_visibility_unchanged", "visible": visible, "count": len(self.ceiling_prims)}
        pxr = lazy.pxr
        with og.sim.editing_usd():
            for prim in self.ceiling_prims:
                if not prim.IsValid():
                    continue
                imageable = pxr.UsdGeom.Imageable(prim)
                if visible:
                    imageable.MakeVisible()
                else:
                    imageable.MakeInvisible()
        self.ceiling_hidden = not visible
        return {"event": "ceiling_visibility", "visible": visible, "count": len(self.ceiling_prims)}

    def mark_ceiling_visibility_dirty(self):
        self.ceiling_visibility_dirty = True

    def sync_ceiling_visibility_for_view(self):
        self.ceiling_visibility_dirty = False
        hide = self.video_source == "viewer" and self.state.camera_mode in CEILING_HIDDEN_CAMERA_MODES
        return self.set_ceiling_visibility(visible=not hide)

    def attach_bridge(self):
        bridge.move_human = self.queue_move_human_delta
        bridge.set_human_input = self.queue_set_human_input
        bridge.rotate_human_heading = self.queue_rotate_human_heading
        bridge.set_camera = self.queue_set_camera
        bridge.run_task = self.queue_run_task
        bridge.reset_scene = self.queue_reset_scene
        bridge.set_video_source = self.queue_set_video_source
        bridge.set_sensor_ranges_visible = self.queue_set_sensor_ranges_visible
        bridge.get_state = self.snapshot

    def queue_move_human_delta(self, dx, dy, dz=0.0, face_movement=True):
        if self.state.robot.busy or self.pending_robot_task:
            return {"event": "move_human_blocked", "reason": "robot task is running"}
        if not self.activity_state.movement_enabled:
            return {"event": "move_human_blocked", "reason": f"resident posture is {self.activity_state.posture}"}
        self.command_queue.put(("move_human_delta", (float(dx), float(dy), float(dz), bool(face_movement))))
        return {"event": "move_human_queued", "dx": dx, "dy": dy, "dz": dz, "face_movement": bool(face_movement)}

    def queue_set_human_input(self, dx, dy, dz=0.0, face_movement=True):
        if self.state.robot.busy or self.pending_robot_task:
            return {"event": "set_human_input_blocked", "reason": "robot task is running"}
        if not self.activity_state.movement_enabled:
            return {"event": "set_human_input_blocked", "reason": f"resident posture is {self.activity_state.posture}"}
        self.command_queue.put(("set_human_input", (float(dx), float(dy), float(dz), bool(face_movement))))
        return {
            "event": "set_human_input_queued",
            "dx": dx,
            "dy": dy,
            "dz": dz,
            "face_movement": bool(face_movement),
        }

    def queue_rotate_human_heading(self, delta_deg):
        if self.state.robot.busy or self.pending_robot_task:
            return {"event": "rotate_human_heading_blocked", "reason": "robot task is running"}
        if not self.activity_state.movement_enabled:
            return {"event": "rotate_human_heading_blocked", "reason": f"resident posture is {self.activity_state.posture}"}
        self.command_queue.put(("rotate_human_heading", (float(delta_deg),)))
        return {"event": "rotate_human_heading_queued", "delta_deg": delta_deg}

    def queue_set_camera(self, mode):
        self.command_queue.put(("set_camera", (str(mode),)))
        return {"event": "set_camera_queued", "mode": mode}

    def queue_run_task(self, task):
        if self.state.robot.busy or self.pending_robot_task:
            return {"event": "task_blocked", "task": task, "reason": "robot already busy"}
        self.pending_robot_task = True
        self.command_queue.put(("run_task", (str(task),)))
        return {"event": "task_queued", "task": task}

    def queue_toggle_replay_pause(self):
        self.command_queue.put(("toggle_replay_pause", ()))
        return {"event": "toggle_replay_pause_queued"}

    def queue_reset_scene(self):
        self.command_queue.put(("reset_scene", ()))
        return {"event": "reset_queued"}

    def queue_restore_runtime_initial_state(self):
        self.command_queue.put(("restore_runtime_initial_state", ()))
        return {"event": "restore_runtime_initial_state_queued"}

    def queue_new_episode(self):
        self.command_queue.put(("new_episode", ()))
        return {"event": "new_episode_queued"}

    def queue_set_video_source(self, source):
        source = str(source)
        if source not in {"viewer", "robot"}:
            source = "viewer"
        self.command_queue.put(("set_video_source", (source,)))
        return {"event": "set_video_source_queued", "source": source}

    def queue_set_sensor_ranges_visible(self, visible):
        self.command_queue.put(("set_sensor_ranges_visible", (bool(visible),)))
        return {"event": "set_sensor_ranges_visible_queued", "visible": bool(visible)}

    def process_commands(self):
        while True:
            try:
                command, args = self.command_queue.get_nowait()
            except queue.Empty:
                break
            if command == "move_human_delta":
                result = self.move_human_delta(*args)
            elif command == "set_human_input":
                result = self.set_human_input(*args)
            elif command == "rotate_human_heading":
                result = self.rotate_human_heading(*args)
            elif command == "set_camera":
                result = self.set_camera(*args)
            elif command == "run_task":
                result = self.run_task(*args)
                self.pending_robot_task = False
            elif command == "toggle_replay_pause":
                result = self.toggle_replay_pause()
            elif command == "reset_scene":
                result = self.reset_scene()
                self.pending_robot_task = False
            elif command == "restore_runtime_initial_state":
                result = self.restore_runtime_initial_state(reason="manual_runtime_reset")
                self.pending_robot_task = False
            elif command == "new_episode":
                result = self.start_episode(zone=self.args.resident_zone, reason="manual", randomize=True)
                self.pending_robot_task = False
            elif command == "set_video_source":
                self.video_source = args[0]
                self.mark_ceiling_visibility_dirty()
                result = {"event": "set_video_source", "source": self.video_source}
            elif command == "set_sensor_ranges_visible":
                self.sensor_ranges_visible = bool(args[0])
                if self.sensor_rig is not None:
                    with og.sim.editing_usd():
                        self.sensor_rig.set_motion_fov_visible(self.sensor_ranges_visible)
                if not self.sensor_ranges_visible:
                    self.sensor_visual_flush_frames = 3
                result = {"event": "set_sensor_ranges_visible", "visible": self.sensor_ranges_visible}
            elif command == "cycle_sensor_layout":
                with og.sim.editing_usd():
                    result = self.cycle_sensor_layout()
            elif command == "export_sensor_layout":
                with og.sim.editing_usd():
                    result = self.export_sensor_layout()
            else:
                result = {"event": "unknown_command", "command": command}
            bridge.log(result.get("event", command), result)


    def _find_robot_rgb_sensors(self):
        if self.robot is None:
            return {}
        sensors = {}
        for key, sensor in self.robot.sensors.items():
            if "rgb" not in sensor.modalities:
                continue
            raw_name = getattr(sensor, "name", None) or key
            name = self.safe_camera_name(raw_name, fallback=f"camera_{len(sensors)}")
            if name in sensors:
                name = f"{name}_{len(sensors)}"
            sensors[name] = sensor
        return sensors

    def capture_video_frame(self, now):
        if now - self.last_video_frame_t < self.video_frame_interval_s:
            return
        self.last_video_frame_t = now
        source = self.video_source
        try:
            if source == "robot" and self.robot_rgb_sensor is not None:
                obs, _ = self.robot_rgb_sensor.get_obs()
            else:
                source = "viewer"
                obs, _ = og.sim.viewer_camera.get_obs()
            if "rgb" not in obs:
                return
            bridge.update_video_frame(rgb_obs_to_jpeg(obs["rgb"]), source)
        except Exception as exc:
            bridge.log("video_frame_error", {"source": source, "reason": str(exc)})

    def _relative_run_path(self, path):
        if path is None or self.episode_logger.run_dir is None:
            return None
        try:
            return str(Path(path).relative_to(self.episode_logger.run_dir))
        except ValueError:
            return str(path)

    def _write_dataset_camera_frame(self, source, obs, frame, sim_t, camera_name=None):
        if "rgb" not in obs:
            return {"available": False, "reason": "rgb_missing"}
        camera_dir = self.episode_logger.camera_dirs.get(source)
        if camera_dir is None:
            return {"available": False, "reason": "camera_dir_missing"}
        safe_name = self.safe_camera_name(camera_name, fallback=source) if camera_name else self.safe_camera_name(source, fallback="camera")
        file_name = f"episode_{self.episode_id:04d}_frame_{int(frame):08d}_{self.camera_frame_seq:06d}.jpg"
        path = camera_dir / safe_name / file_name
        info = write_rgb_obs_jpeg(
            obs["rgb"],
            path,
            quality=int(self.args.camera_log_quality),
            width=int(self.args.camera_log_width),
        )
        info.update(
            {
                "available": True,
                "source": source,
                "camera_name": safe_name,
                "path": self._relative_run_path(info["path"]),
                "frame": int(frame),
                "sim_time_s": float(sim_t),
                "sequence": int(self.camera_frame_seq),
            }
        )
        return info

    def capture_dataset_camera_frames(self, now, sim_t, frame):
        refs = {
            "top": None,
            "robot": None,
        }
        if (
            not self.camera_log_enabled
            or self.episode_id <= 0
            or not self.episode_logger.enabled
            or self.episode_phase() != "task_running"
            or now - self.last_camera_log_t < self.camera_log_interval_s
        ):
            return refs

        self.last_camera_log_t = now
        self.camera_frame_seq += 1
        self.episode_metrics["camera_sample_count"] += 1

        for source in self.camera_log_sources:
            try:
                if source == "robot":
                    if not self.robot_rgb_sensors:
                        refs[source] = {"available": False, "reason": "robot_rgb_sensor_missing"}
                    else:
                        refs[source] = {}
                        for camera_name, sensor in self.robot_rgb_sensors.items():
                            try:
                                obs, _ = sensor.get_obs()
                                refs[source][camera_name] = self._write_dataset_camera_frame(
                                    source,
                                    obs,
                                    frame,
                                    sim_t,
                                    camera_name=camera_name,
                                )
                            except Exception as camera_exc:
                                refs[source][camera_name] = {
                                    "available": False,
                                    "source": source,
                                    "camera_name": camera_name,
                                    "reason": str(camera_exc),
                                }
                elif source == "top":
                    if self.state.camera_mode != "overview":
                        refs[source] = {"available": False, "reason": "viewer_not_in_overview_mode"}
                    else:
                        obs, _ = og.sim.viewer_camera.get_obs()
                        refs[source] = self._write_dataset_camera_frame(source, obs, frame, sim_t)
                else:
                    refs[source] = {"available": False, "reason": f"unknown_source:{source}"}
            except Exception as exc:
                refs[source] = {"available": False, "reason": str(exc)}

            if source == "robot" and isinstance(refs[source], dict):
                if "available" in refs[source]:
                    if refs[source].get("available"):
                        self.camera_frame_counts[source] += 1
                    else:
                        self.camera_frame_missing_counts[source] += 1
                else:
                    available_count = sum(
                        1 for item in refs[source].values() if isinstance(item, dict) and item.get("available")
                    )
                    missing_count = sum(
                        1 for item in refs[source].values() if isinstance(item, dict) and not item.get("available")
                    )
                    self.camera_frame_counts[source] += available_count
                    self.camera_frame_missing_counts[source] += missing_count
                self.episode_metrics["camera_frame_counts"][source] = self.camera_frame_counts[source]
                self.episode_metrics["camera_frame_missing_counts"][source] = self.camera_frame_missing_counts[source]
            elif refs[source] and refs[source].get("available"):
                self.camera_frame_counts[source] += 1
                self.episode_metrics["camera_frame_counts"][source] = self.camera_frame_counts[source]
            else:
                self.camera_frame_missing_counts[source] += 1
                self.episode_metrics["camera_frame_missing_counts"][source] = self.camera_frame_missing_counts[source]

        return refs

    def configure_keyboard(self):
        KeyboardEventHandler.initialize()
        KeyboardEventHandler.add_keyboard_callback(lazy.carb.input.KeyboardInput.ESCAPE, lambda: og.shutdown())
        for key_name in ("W", "A", "S", "D", "UP", "LEFT", "DOWN", "RIGHT"):
            key = getattr(lazy.carb.input.KeyboardInput, key_name, None)
            if key is not None:
                KeyboardEventHandler.add_keyboard_callback(key, lambda name=key_name: self.queue_viewport_move(name))
        for key_name, delta in {"Q": VIEWPORT_ROTATION_STEP_DEG, "E": -VIEWPORT_ROTATION_STEP_DEG}.items():
            key = getattr(lazy.carb.input.KeyboardInput, key_name, None)
            if key is not None:
                KeyboardEventHandler.add_keyboard_callback(key, lambda d=delta: self.queue_viewport_rotate(d))
        for key_name, mode in VIEWPORT_CAMERA_KEYS.items():
            key = getattr(lazy.carb.input.KeyboardInput, key_name, None)
            if key is not None:
                KeyboardEventHandler.add_keyboard_callback(key, lambda m=mode: self.queue_set_camera(m))
        key = getattr(lazy.carb.input.KeyboardInput, "C", None)
        if key is not None:
            KeyboardEventHandler.add_keyboard_callback(key, self.queue_cycle_viewport_camera)
        key = getattr(lazy.carb.input.KeyboardInput, "F", None)
        if key is not None:
            KeyboardEventHandler.add_keyboard_callback(key, self.queue_toggle_sensor_ranges)
        key = getattr(lazy.carb.input.KeyboardInput, "L", None)
        if key is not None:
            KeyboardEventHandler.add_keyboard_callback(key, self.queue_cycle_sensor_layout)
        key = getattr(lazy.carb.input.KeyboardInput, "K", None)
        if key is not None:
            KeyboardEventHandler.add_keyboard_callback(key, self.queue_export_sensor_layout)
        key = getattr(lazy.carb.input.KeyboardInput, "R", None)
        if key is not None:
            KeyboardEventHandler.add_keyboard_callback(key, self.queue_reset_scene)
        key = getattr(lazy.carb.input.KeyboardInput, "I", None)
        if key is not None:
            KeyboardEventHandler.add_keyboard_callback(key, self.queue_restore_runtime_initial_state)
        key = getattr(lazy.carb.input.KeyboardInput, "P", None)
        if key is not None:
            KeyboardEventHandler.add_keyboard_callback(key, self.queue_toggle_replay_pause)
        key = getattr(lazy.carb.input.KeyboardInput, "N", None)
        if key is not None:
            KeyboardEventHandler.add_keyboard_callback(key, self.queue_new_episode)
        key = getattr(lazy.carb.input.KeyboardInput, "T", None)
        if key is not None:
            KeyboardEventHandler.add_keyboard_callback(key, lambda: self.queue_run_task("deliver_item"))
        key = getattr(lazy.carb.input.KeyboardInput, "Y", None)
        if key is not None:
            KeyboardEventHandler.add_keyboard_callback(key, lambda: self.queue_run_task("laundry"))
        self.print_viewport_controls()

    def print_viewport_controls(self):
        print(
            "\nHomeSense viewport controls\n"
            "  1: Top overview camera\n"
            "  2: Resident follow camera\n"
            "  3: Free viewer camera\n"
            "  C: Cycle camera mode\n"
            "  W/A/S/D or arrow keys: Move resident in overview; move relative to resident in resident follow\n"
            "  Q/E: Rotate resident in resident follow\n"
            "  F: Toggle motion sensor ranges\n"
            "  L: Cycle sensor layout (current / dense / sparse)\n"
            "  K: Export active sensor layout after viewport edits\n"
            "  I: Restore the robot/object runtime initial state for replay\n"
            "  P: Pause/resume the active HDF5 replay in place\n"
            "  N: Start a randomized data-collection episode\n"
            "  T/Y: Trigger deliver-item HDF5 replay selected by activity / laundry placeholder\n"
            "  R: Reset scene and restore runtime initial state\n"
            "  Esc: Quit\n",
            flush=True,
        )

    def create_viewport_hud(self):
        if self.args.disable_viewport_hud:
            return
        try:
            import omni.ui as ui
        except Exception as exc:
            bridge.log("viewport_hud_unavailable", {"reason": str(exc)})
            return
        try:
            self.hud_window = ui.Window(
                "HomeSense Live Context",
                width=360,
                height=210,
                position_x=1030,
                position_y=520,
                visible=True,
            )
        except TypeError:
            self.hud_window = ui.Window("HomeSense Live Context", width=360, height=210, visible=True)
        for attr, value in {"position_x": 1030, "position_y": 520}.items():
            try:
                setattr(self.hud_window, attr, value)
            except Exception:
                pass
        with self.hud_window.frame:
            with ui.VStack(spacing=6, height=0):
                ui.Label("HomeSense Digital Twin Context", height=24)
                self.hud_labels["episode"] = ui.Label("Episode: --")
                self.hud_labels["zone"] = ui.Label("Resident: --")
                self.hud_labels["motion"] = ui.Label("Motion: --")
                self.hud_labels["activity"] = ui.Label("Activity: --")
                self.hud_labels["virtual"] = ui.Label("Virtual sensors: --")
                self.hud_labels["task"] = ui.Label("Robot task: --")
                self.hud_labels["logging"] = ui.Label("Dataset: --")

    def update_viewport_hud(self, now):
        if not self.hud_labels or now - self.last_hud_update_t < 0.25:
            return
        self.last_hud_update_t = now
        virtual_items = [
            f"{key}={value}"
            for key, value in sorted(self.activity_state.virtual_sensors.items())
        ]
        virtual_summary = ", ".join(virtual_items[:4]) if virtual_items else "--"
        if len(virtual_items) > 4:
            virtual_summary += f", +{len(virtual_items) - 4}"
        context = self.resident_context or {}
        confidence = context.get("confidence")
        confidence_text = f"{float(confidence):.2f}" if confidence is not None else "--"
        dataset_dir = str(self.episode_logger.run_dir) if self.episode_logger.run_dir else "disabled"
        replay_text = self.hdf5_replay_id or "--"
        if self.robot_replay_active and self.hdf5_replay_actions is not None:
            replay_text += f" step={int(self.robot_replay_step)}/{int(len(self.hdf5_replay_actions))}"
            if self.robot_replay_paused:
                replay_text += " paused"
        values = {
            "episode": f"Episode: {self.episode_id} / zone={self.episode_zone}",
            "zone": f"Resident: {self.state.human.zone} pos={self._short_vec(self.state.human.position)}",
            "motion": f"Motion: {self.state.motion.active_sensor_id or '--'} detected={self.state.motion.detected} layout={self.sensor_layout}",
            "activity": f"Activity: {self.activity_state.activity_id or '--'} confidence={confidence_text}",
            "virtual": f"Virtual sensors: {virtual_summary}",
            "task": f"Robot task: {self.state.robot.task or '--'} status={self.state.robot.status} replay={replay_text}",
            "logging": f"Dataset: {dataset_dir}",
        }
        for key, text in values.items():
            label = self.hud_labels.get(key)
            if label is not None:
                label.text = text

    def _short_vec(self, values):
        try:
            return "[" + ", ".join(f"{float(value):.2f}" for value in values[:3]) + "]"
        except Exception:
            return "--"

    def queue_viewport_move(self, key_name):
        if not self.activity_state.movement_enabled:
            self.clear_viewport_input()
            return {"event": "viewport_move_blocked", "reason": f"resident posture is {self.activity_state.posture}"}
        dx, dy, face_movement = self.viewport_move_input(key_name)
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            self.clear_viewport_input()
            return {"event": "viewport_move_ignored", "camera_mode": self.state.camera_mode, "key": key_name}
        self.viewport_input_active = True
        self.viewport_input_expires_t = time() + VIEWPORT_INPUT_HOLD_S
        self.viewport_input_vector = (dx, dy, 0.0)
        self.viewport_input_face_movement = bool(face_movement)
        self.apply_viewport_input()
        return {
            "event": "viewport_input",
            "key": key_name,
            "dx": dx,
            "dy": dy,
            "face_movement": bool(face_movement),
        }

    def clear_viewport_input(self):
        if not self.viewport_input_active:
            return
        self.viewport_input_active = False
        self.viewport_input_vector = (0.0, 0.0, 0.0)
        self.human_input_vector = (0.0, 0.0, 0.0)
        if self.dummy_root is not None:
            self.human_target_pos = _get_dummy_position(self.dummy_root).tolist()

    def sync_viewport_input(self, now):
        if not self.viewport_input_active:
            return
        if self.state.robot.busy or not self.activity_state.movement_enabled or now > self.viewport_input_expires_t:
            self.clear_viewport_input()
            return
        self.apply_viewport_input()

    def apply_viewport_input(self):
        vector = th.tensor(self.viewport_input_vector, dtype=th.float32)
        length = float(th.norm(vector))
        if length < 1e-5:
            self.clear_viewport_input()
            return
        normalized = (vector / length).tolist()
        self.human_input_vector = tuple(float(v) for v in normalized)
        self.human_target_pos = _get_dummy_position(self.dummy_root).tolist()
        if self.viewport_input_face_movement:
            self.human_heading_deg = self.heading_from_world_vector(
                self.human_input_vector[0],
                self.human_input_vector[1],
            )

    def queue_viewport_rotate(self, delta_deg):
        if self.state.camera_mode != "resident":
            return {"event": "viewport_rotate_ignored", "camera_mode": self.state.camera_mode}
        self.clear_viewport_input()
        return self.queue_rotate_human_heading(delta_deg)

    def viewport_move_input(self, key_name):
        mode = self.state.camera_mode
        key_name = str(key_name).upper()
        key_alias = {"UP": "W", "LEFT": "A", "DOWN": "S", "RIGHT": "D"}.get(key_name, key_name)
        if mode == "resident":
            heading = math.radians(self.human_heading_deg)
            forward = [-math.sin(heading), math.cos(heading)]
            right = [math.cos(heading), math.sin(heading)]
            if key_alias == "W":
                return forward[0], forward[1], False
            if key_alias == "S":
                return -forward[0], -forward[1], False
            if key_alias == "A":
                return -right[0], -right[1], False
            if key_alias == "D":
                return right[0], right[1], False
            return 0.0, 0.0, False
        if mode == "overview":
            if key_alias == "W":
                return 1.0, 0.0, True
            if key_alias == "S":
                return -1.0, 0.0, True
            if key_alias == "A":
                return 0.0, 1.0, True
            if key_alias == "D":
                return 0.0, -1.0, True
        return 0.0, 0.0, False

    def queue_cycle_viewport_camera(self):
        self.clear_viewport_input()
        try:
            idx = self.viewport_camera_modes.index(self.state.camera_mode)
        except ValueError:
            idx = 0
        return self.queue_set_camera(self.viewport_camera_modes[(idx + 1) % len(self.viewport_camera_modes)])

    def queue_toggle_sensor_ranges(self):
        return self.queue_set_sensor_ranges_visible(not self.sensor_ranges_visible)

    def queue_cycle_sensor_layout(self):
        self.command_queue.put(("cycle_sensor_layout", ()))
        return {"event": "cycle_sensor_layout_queued"}

    def queue_export_sensor_layout(self):
        self.command_queue.put(("export_sensor_layout", ()))
        return {"event": "export_sensor_layout_queued"}

    def export_sensor_layout(self):
        if self.sensor_rig is None:
            return {"event": "sensor_layout_export_failed", "reason": "sensor rig is not initialized"}
        specs = self.sensor_rig.export_active_motion_sensor_specs()
        export_dir = REPO_ROOT / "logs" / "sensor_layout_exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = utc_now_iso().replace(":", "").replace("-", "").replace(".", "")
        path = export_dir / f"{stamp}_{self.sensor_layout}.yaml"
        payload = {
            "scene_model": self.scene_profile.scene_model if self.scene_profile else self.args.scene_model,
            "sensor_layout": self.sensor_layout,
            "motion_sensors": specs,
        }
        path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return {
            "event": "sensor_layout_exported",
            "sensor_layout": self.sensor_layout,
            "active_motion_sensor_count": len(specs),
            "path": str(path),
        }

    def move_human_delta(self, dx, dy, dz=0.0, face_movement=True):
        if self.state.robot.busy:
            return {"event": "move_human_blocked", "reason": "robot task is running"}
        dx = max(-HUMAN_COMMAND_LIMIT_M, min(HUMAN_COMMAND_LIMIT_M, float(dx)))
        dy = max(-HUMAN_COMMAND_LIMIT_M, min(HUMAN_COMMAND_LIMIT_M, float(dy)))
        dz = max(-HUMAN_COMMAND_LIMIT_M, min(HUMAN_COMMAND_LIMIT_M, float(dz)))
        base_pos = self.human_target_pos or _get_dummy_position(self.dummy_root).tolist()
        next_pos = [base_pos[0] + dx, base_pos[1] + dy, base_pos[2] + dz]
        blocked_by = self._movement_blocker(next_pos)
        if blocked_by is not None:
            self.read_sensors()
            return {"event": "move_human_blocked", "reason": "collision", "blocked_by": blocked_by, "position": base_pos}
        self.human_target_pos = next_pos
        if face_movement and (abs(dx) > 1e-5 or abs(dy) > 1e-5):
            self.human_heading_deg = self.heading_from_world_vector(dx, dy)
        self.read_sensors()
        return {
            "event": "move_human_delta",
            "target_position": next_pos,
            "dx": dx,
            "dy": dy,
            "dz": dz,
            "face_movement": bool(face_movement),
            "heading_deg": self.human_heading_deg,
        }

    def set_human_input(self, dx, dy, dz=0.0, face_movement=True):
        if self.state.robot.busy:
            return {"event": "set_human_input_blocked", "reason": "robot task is running"}
        vector = th.tensor([float(dx), float(dy), float(dz)], dtype=th.float32)
        length = float(th.norm(vector))
        if length < 1e-5:
            self.human_input_vector = (0.0, 0.0, 0.0)
            self.human_target_pos = _get_dummy_position(self.dummy_root).tolist()
        else:
            normalized = (vector / length).tolist()
            self.human_input_vector = tuple(float(v) for v in normalized)
            self.human_target_pos = _get_dummy_position(self.dummy_root).tolist()
            if face_movement:
                self.human_heading_deg = self.heading_from_world_vector(
                    self.human_input_vector[0],
                    self.human_input_vector[1],
                )
        self.read_sensors()
        return {
            "event": "set_human_input",
            "dx": self.human_input_vector[0],
            "dy": self.human_input_vector[1],
            "dz": self.human_input_vector[2],
            "face_movement": bool(face_movement),
            "heading_deg": self.human_heading_deg,
        }

    def rotate_human_heading(self, delta_deg):
        if self.state.robot.busy:
            return {"event": "rotate_human_heading_blocked", "reason": "robot task is running"}
        self.human_heading_deg = ((self.human_heading_deg + float(delta_deg) + 180.0) % 360.0) - 180.0
        current_pos = _get_dummy_position(self.dummy_root).tolist()
        self.human_target_pos = current_pos
        self._set_human_pose(current_pos, self.human_heading_deg)
        self.read_sensors()
        return {"event": "rotate_human_heading", "heading_deg": self.human_heading_deg, "delta_deg": delta_deg}

    def _movement_blocker(self, position):
        x, y, z = position
        if self.scene_bounds is not None:
            min_pt = self.scene_bounds["min"]
            max_pt = self.scene_bounds["max"]
            if (
                x < min_pt[0] - 0.25
                or x > max_pt[0] + 0.25
                or y < min_pt[1] - 0.25
                or y > max_pt[1] + 0.25
            ):
                return "scene_bounds"
        for obstacle in self.collision_obstacles:
            min_pt = obstacle["min"]
            max_pt = obstacle["max"]
            if self._is_doorless_portal_wall_clearance(position, obstacle["path"]):
                continue
            if z > max_pt[2] + 0.05:
                continue
            nearest_x = max(min_pt[0], min(x, max_pt[0]))
            nearest_y = max(min_pt[1], min(y, max_pt[1]))
            if (x - nearest_x) ** 2 + (y - nearest_y) ** 2 < HUMAN_RADIUS_M**2:
                return obstacle["path"]
        return None

    def _inside_scene_bounds(self, position, margin=0.0):
        if self.scene_bounds is None:
            return True
        x, y, _ = position
        min_pt = self.scene_bounds["min"]
        max_pt = self.scene_bounds["max"]
        return (
            min_pt[0] - margin <= x <= max_pt[0] + margin
            and min_pt[1] - margin <= y <= max_pt[1] + margin
        )

    def _is_doorless_portal_wall_clearance(self, position, obstacle_path):
        if not (self.args.doorless_scene and self.scene_profile is not None and self.scene_profile.doorless_portals):
            return False
        if "wall" not in obstacle_path.lower():
            return False
        x, y = float(position[0]), float(position[1])
        for portal in self.scene_profile.doorless_portals:
            px, py = portal["position"]
            radius = float(portal["radius_m"])
            if (x - px) ** 2 + (y - py) ** 2 <= radius**2:
                return True
        return False

    def find_nearest_free_position(self, preferred):
        preferred = [float(preferred[0]), float(preferred[1]), float(preferred[2])]
        if self._inside_scene_bounds(preferred) and self._movement_blocker(preferred) is None:
            return preferred
        step = 0.18
        max_steps = 16
        best = None
        best_dist = float("inf")
        for ix in range(-max_steps, max_steps + 1):
            for iy in range(-max_steps, max_steps + 1):
                if ix == 0 and iy == 0:
                    continue
                candidate = [preferred[0] + ix * step, preferred[1] + iy * step, preferred[2]]
                if not self._inside_scene_bounds(candidate):
                    continue
                if self._movement_blocker(candidate) is not None:
                    continue
                dist = (candidate[0] - preferred[0]) ** 2 + (candidate[1] - preferred[1]) ** 2
                if dist < best_dist:
                    best = candidate
                    best_dist = dist
        if best is None:
            bridge.log("human_spawn_free_position_failed", {"preferred": preferred})
            return preferred
        bridge.log("human_spawn_adjusted", {"preferred": preferred, "position": best, "distance_m": math.sqrt(best_dist)})
        return best

    def choose_episode_zone(self, requested_zone):
        if self.scene_profile is None or not self.scene_profile.zones:
            return None
        zone_names = list(self.scene_profile.zones)
        if requested_zone and requested_zone != "random":
            if requested_zone not in self.scene_profile.zones:
                bridge.log(
                    "episode_zone_fallback",
                    {"requested_zone": requested_zone, "available_zones": zone_names, "fallback": "random"},
                )
            else:
                return requested_zone
        return self.rng.choice(zone_names)

    def sample_resident_position_for_zone(self, zone_name, activity_spawn_points=None, collision_check=True):
        if self.scene_profile is None or zone_name not in self.scene_profile.zones:
            return self.human_start_position(PRESETS[self.args.preset])
        zone = self.scene_profile.zones[zone_name]
        center = zone.get("center")
        if not center:
            return self.human_start_position(PRESETS[self.args.preset])
        spawn_points = activity_spawn_points or zone.get("spawn_points") or []
        if spawn_points:
            shuffled = [list(point) for point in spawn_points]
            self.rng.shuffle(shuffled)
            for point in shuffled:
                base = [float(point[0]), float(point[1]), float(point[2]) if len(point) > 2 else 0.0]
                if not collision_check and self._inside_scene_bounds(base):
                    return base
                radius = float(zone.get("spawn_radius_m", 0.0))
                candidates = [base]
                if radius > 1e-4:
                    for _ in range(12):
                        angle = self.rng.uniform(0.0, math.tau)
                        distance = radius * math.sqrt(self.rng.random())
                        candidates.append([
                            base[0] + math.cos(angle) * distance,
                            base[1] + math.sin(angle) * distance,
                            base[2],
                        ])
                for candidate in candidates:
                    if self._inside_scene_bounds(candidate) and (
                        not collision_check or self._movement_blocker(candidate) is None
                    ):
                        return candidate
            bridge.log("episode_spawn_points_blocked", {"zone": zone_name, "spawn_points": spawn_points})
        radius = float(zone.get("spawn_radius_m", 0.5))
        z = float(center[2]) if len(center) > 2 else float(self.human_start_position(PRESETS[self.args.preset])[2])
        fallback = [float(center[0]), float(center[1]), z]
        for _ in range(64):
            angle = self.rng.uniform(0.0, math.tau)
            distance = radius * math.sqrt(self.rng.random())
            candidate = [
                float(center[0]) + math.cos(angle) * distance,
                float(center[1]) + math.sin(angle) * distance,
                z,
            ]
            if self._inside_scene_bounds(candidate) and (
                not collision_check or self._movement_blocker(candidate) is None
            ):
                return candidate
        return self.find_nearest_free_position(fallback)

    def current_robot_position(self):
        if self.robot is None:
            return None
        try:
            position, _ = self.robot.get_position_orientation()
            return [float(v) for v in position.tolist()]
        except Exception:
            return None

    def apply_robot_initial_pose(self):
        if self.robot is None or self.scene_profile is None:
            return
        spec = self.scene_profile.robot or {}
        if not spec:
            return
        position = spec.get("start_position")
        if position is None:
            return
        yaw_deg = float(spec.get("start_yaw_deg", 0.0))
        orientation = spec.get("start_orientation_xyzw")
        if orientation is None:
            orientation_tensor = T.euler2quat(th.tensor([0.0, 0.0, math.radians(yaw_deg)], dtype=th.float32))
        else:
            orientation_tensor = th.tensor([float(v) for v in orientation], dtype=th.float32)
        position_tensor = th.tensor([float(v) for v in position], dtype=th.float32)
        try:
            self.robot.set_position_orientation(position=position_tensor, orientation=orientation_tensor)
            bridge.log(
                "robot_configured_initial_pose",
                {
                    "position": [float(v) for v in position_tensor.tolist()],
                    "yaw_deg": yaw_deg,
                    "orientation_xyzw": [float(v) for v in orientation_tensor.tolist()],
                    "forward_axis": spec.get("forward_axis", "+X"),
                    "source": "scene_profile.robot",
                },
            )
        except Exception as exc:
            bridge.log("robot_initial_pose_failed", {"reason": str(exc), "source": "scene_profile.robot"})

    def add_scene_profile_objects(self):
        if self.scene_profile is None or not self.scene_profile.demo_objects:
            return
        object_names = set(getattr(self.env.scene.object_registry, "object_names", []))
        for spec in self.scene_profile.demo_objects:
            spec = dict(spec)
            name = str(spec["name"])
            replay_pose = ((self.hdf5_replay_config or {}).get("object_poses") or {}).get(name)
            if replay_pose is not None:
                offset = spec.get("replay_pose_offset", [0.0, 0.0, 0.0])
                spec["position"] = [float(v) + float(offset[i]) for i, v in enumerate(replay_pose["position"])]
                spec["orientation_xyzw"] = replay_pose["orientation_xyzw"]
            if name in object_names:
                continue
            try:
                obj = DatasetObject(
                    name=name,
                    category=str(spec["category"]),
                    model=str(spec["model"]),
                    scale=spec.get("scale", [1.0, 1.0, 1.0]),
                    expected_file_hash=spec.get("expected_file_hash"),
                )
                self.env.scene.add_object(obj)
                obj.set_position_orientation(
                    position=th.tensor([float(v) for v in spec["position"]], dtype=th.float32),
                    orientation=th.tensor([float(v) for v in spec["orientation_xyzw"]], dtype=th.float32),
                )
                bridge.log(
                    "demo_object_added",
                    {
                        "name": name,
                        "category": spec["category"],
                        "model": spec["model"],
                        "position": spec["position"],
                        "source": spec.get("source", "scene_profile.demo_objects"),
                    },
                )
            except Exception as exc:
                bridge.log("demo_object_add_failed", {"name": name, "reason": str(exc)})
                self.add_scene_profile_object_placeholder(spec, reason=str(exc))

    def sync_hdf5_replay_object_poses(self, reason):
        if self.hdf5_replay_config is None:
            return
        object_states = self.hdf5_replay_config.get("object_states") or {}
        restored = []
        missing = []
        failed = []
        for name, state in object_states.items():
            try:
                obj = self.env.scene.object_registry("name", name)
            except Exception:
                obj = None
            if obj is None:
                missing.append(name)
                continue
            root_link = state.get("root_link") or {}
            if root_link.get("pos") is None or root_link.get("ori") is None:
                continue
            offset = [0.0, 0.0, 0.0]
            if self.scene_profile is not None:
                for spec in self.scene_profile.demo_objects:
                    if str(spec.get("name")) == name:
                        offset = spec.get("replay_pose_offset", offset)
                        break
            target_position = [float(v) + float(offset[i]) for i, v in enumerate(root_link["pos"])]
            position = th.tensor(target_position, dtype=th.float32)
            orientation = th.tensor([float(v) for v in root_link["ori"]], dtype=th.float32)
            try:
                obj.set_position_orientation(position=position, orientation=orientation)
                if hasattr(obj, "set_linear_velocity"):
                    obj.set_linear_velocity(th.zeros(3, dtype=th.float32))
                if hasattr(obj, "set_angular_velocity"):
                    obj.set_angular_velocity(th.zeros(3, dtype=th.float32))
                if hasattr(obj, "keep_still"):
                    obj.keep_still()
                actual_pos, actual_ori = obj.get_position_orientation()
                restored.append(
                    {
                        "name": name,
                        "target_position": target_position,
                        "hdf5_position": root_link["pos"],
                        "replay_pose_offset": offset,
                        "actual_position": [float(v) for v in actual_pos.tolist()],
                        "actual_orientation_xyzw": [float(v) for v in actual_ori.tolist()],
                    }
                )
            except Exception as exc:
                failed.append({"name": name, "error": str(exc)})
        sample_names = {TASK_EVAL_OBJECT_NAME}
        sample_names.update(item["name"] for item in restored[:5])
        bridge.log(
            "hdf5_replay_object_poses_synced",
            {
                "reason": reason,
                "requested_count": len(object_states),
                "restored_count": len(restored),
                "missing_count": len(missing),
                "failed_count": len(failed),
                "missing": missing[:16],
                "failed": failed[:8],
                "samples": [item for item in restored if item["name"] in sample_names][:8],
                "velocity_policy": "zero_keep_still",
            },
        )

    def sync_hdf5_replay_robot_state(self, reason):
        if self.hdf5_replay_config is None:
            return
        robot_state = self.hdf5_replay_config.get("robot_state") or {}
        root_link = robot_state.get("root_link") or {}
        try:
            if root_link.get("pos") is not None and root_link.get("ori") is not None:
                self.robot.set_position_orientation(
                    position=th.tensor([float(v) for v in root_link["pos"]], dtype=th.float32),
                    orientation=th.tensor([float(v) for v in root_link["ori"]], dtype=th.float32),
                )
            if robot_state.get("joint_pos") is not None and hasattr(self.robot, "set_joint_positions"):
                self.robot.set_joint_positions(
                    th.tensor([float(v) for v in robot_state["joint_pos"]], dtype=th.float32),
                    drive=False,
                )
            if robot_state.get("joint_vel") is not None and hasattr(self.robot, "set_joint_velocities"):
                self.robot.set_joint_velocities(
                    th.tensor([float(v) for v in robot_state["joint_vel"]], dtype=th.float32),
                    drive=False,
                )
            if root_link.get("lin_vel") is not None and hasattr(self.robot, "set_linear_velocity"):
                self.robot.set_linear_velocity(th.tensor([float(v) for v in root_link["lin_vel"]], dtype=th.float32))
            if root_link.get("ang_vel") is not None and hasattr(self.robot, "set_angular_velocity"):
                self.robot.set_angular_velocity(th.tensor([float(v) for v in root_link["ang_vel"]], dtype=th.float32))
            actual_pos, actual_ori = self.robot.get_position_orientation()
            bridge.log(
                "hdf5_replay_robot_state_synced",
                {
                    "reason": reason,
                    "target_position": root_link.get("pos"),
                    "actual_position": [float(v) for v in actual_pos.tolist()],
                    "actual_orientation_xyzw": [float(v) for v in actual_ori.tolist()],
                    "joint_count": len(robot_state.get("joint_pos") or []),
                },
            )
        except Exception as exc:
            bridge.log("hdf5_replay_robot_state_sync_failed", {"reason": reason, "error": str(exc)})

    def stabilize_robot_after_reset(self):
        if self.robot is None:
            return
        try:
            if hasattr(self.robot, "set_linear_velocity"):
                self.robot.set_linear_velocity(th.zeros(3, dtype=th.float32))
            if hasattr(self.robot, "set_angular_velocity"):
                self.robot.set_angular_velocity(th.zeros(3, dtype=th.float32))
            if hasattr(self.robot, "get_joint_positions") and hasattr(self.robot, "set_joint_velocities"):
                joint_positions = self.robot.get_joint_positions()
                if joint_positions is not None:
                    self.robot.set_joint_velocities(th.zeros_like(joint_positions), drive=False)
            if hasattr(self.robot, "keep_still"):
                self.robot.keep_still()
        except Exception as exc:
            bridge.log("robot_reset_stabilization_failed", {"reason": str(exc)})

    def settle_runtime_reset(self, steps=2):
        if self.env is None or self.zero_action is None:
            return
        for _ in range(max(int(steps), 0)):
            try:
                self.env.step(action=self.zero_action, n_render_iterations=1)
            except TypeError:
                self.env.step(action=self.zero_action)

    def restore_runtime_initial_state(self, reason="runtime_reset"):
        interrupted_eval = None
        if reason in {"manual_runtime_reset", "scene_reset"}:
            interrupted_eval = self.finalize_task_evaluation_for_interruption(f"interrupted_by_{reason}")
        self.robot_replay_active = False
        self.robot_replay_paused = False
        self.robot_replay_step = 0
        self.robot_task_end_t = None
        self.pending_robot_task = False
        self.state.robot.status = "idle"
        self.state.robot.task = None
        self.state.robot.replay_id = None
        self.hdf5_state_replay_failed = False
        robot_source = None
        object_source = None
        reset_invoked = False
        if self.robot is not None and hasattr(self.robot, "reset"):
            try:
                self.robot.reset()
                reset_invoked = True
            except Exception as exc:
                bridge.log("robot_reset_failed", {"reason": reason, "error": str(exc)})
        if self.hdf5_replay_config is not None and (self.hdf5_replay_config.get("robot_state") or {}):
            self.sync_hdf5_replay_robot_state(reason)
            robot_source = "hdf5_replay.robot_state"
        else:
            self.apply_robot_initial_pose()
            robot_source = "scene_profile.robot"
        self.stabilize_robot_after_reset()
        if self.hdf5_replay_config is not None and (self.hdf5_replay_config.get("object_states") or {}):
            self.sync_hdf5_replay_object_poses(reason)
            object_source = "hdf5_replay.object_states"
        self.last_robot_action_record = {
            "source": "zero",
            "step": None,
            "vector": None,
            "controller": None,
            "normalized": None,
        }
        self.settle_runtime_reset()
        payload = {
            "event": "runtime_initial_state_restored",
            "reason": reason,
            "robot_source": robot_source,
            "object_source": object_source,
            "robot_reset_invoked": reset_invoked,
            "replay_stopped": True,
            "scene_variant": self.active_scene_variant,
            "scene_file": None if self.active_scene_file is None else str(self.active_scene_file),
            "task_evaluation": interrupted_eval,
        }
        bridge.log("runtime_initial_state_restored", payload)
        return payload

    def add_scene_profile_object_placeholder(self, spec, reason: str):
        name = f"{spec['name']}_placeholder"
        try:
            obj = PrimitiveObject(
                name=name,
                primitive_type="Cylinder",
                category=str(spec.get("category", "demo_object")),
                radius=0.035,
                height=0.12,
                fixed_base=True,
                visual_only=False,
                rgba=(0.95, 0.95, 0.98, 1.0),
            )
            self.env.scene.add_object(obj)
            obj.set_position_orientation(
                position=th.tensor([float(v) for v in spec["position"]], dtype=th.float32),
                orientation=th.tensor([float(v) for v in spec["orientation_xyzw"]], dtype=th.float32),
            )
            bridge.log(
                "demo_object_placeholder_added",
                {
                    "name": name,
                    "for": spec["name"],
                    "position": spec["position"],
                    "reason": reason,
                },
            )
        except Exception as placeholder_exc:
            bridge.log(
                "demo_object_placeholder_failed",
                {"name": name, "reason": str(placeholder_exc), "original_reason": reason},
            )

    def current_robot_pose(self):
        if self.robot is None:
            return None
        try:
            position, quat = self.robot.get_position_orientation()
            euler = T.quat2euler(quat)
            yaw_rad = float(euler[2])
            yaw_deg = math.degrees(yaw_rad)
            forward = T.quat_apply(quat, th.tensor([1.0, 0.0, 0.0], dtype=th.float32))
            return {
                "position": [float(v) for v in position.tolist()],
                "orientation_xyzw": [float(v) for v in quat.tolist()],
                "euler_rpy_rad": [float(v) for v in euler.tolist()],
                "yaw_deg": yaw_deg,
                "forward_xy": [float(forward[0]), float(forward[1])],
            }
        except Exception as exc:
            return {"available": False, "reason": str(exc)}

    def current_robot_resident_distance(self):
        robot_pos = self.current_robot_position()
        if robot_pos is None or self.dummy_root is None:
            return None
        human_pos = _get_dummy_position(self.dummy_root).tolist()
        return math.sqrt((robot_pos[0] - human_pos[0]) ** 2 + (robot_pos[1] - human_pos[1]) ** 2)

    def ground_truth_resident_zone(self):
        return self.activity_state.ground_truth_zone or self.episode_zone or "unknown"

    def infer_scenario_type(self):
        zone = self.ground_truth_resident_zone()
        activity_id = self.activity_state.activity_id
        distance = self.current_robot_resident_distance()
        if zone == "configured_start":
            return "configured_start"
        if distance is not None and distance < 1.5:
            return "resident_near_robot"
        if activity_id in {"doing_laundry", "checking_laundry"} or zone in {"laundry", "utility_room"}:
            return "laundry_ready"
        if activity_id in {"showering", "bathroom_visit"} or zone == "bathroom":
            return "bathroom_occupied"
        if activity_id in {"arriving_home", "entry_living"} or zone in {"entry", "entry_living"}:
            return "arrival_context"
        if activity_id:
            return "activity_context"
        return "normal_context"

    def risk_snapshot(self):
        distance = self.current_robot_resident_distance()
        if distance is None:
            level = "unknown"
        elif distance < 1.0:
            level = "high"
        elif distance < 2.0:
            level = "medium"
        else:
            level = "low"
        return {
            "level": level,
            "robot_resident_distance_m": distance,
            "resident_near_robot": bool(distance is not None and distance < 1.5),
            "near_collision": bool(distance is not None and distance < 0.75),
            "collision": False,
            "resident_in_robot_path": None,
            "robot_should_yield": None,
            "human_aware_planning_label": None,
            "min_robot_resident_distance_m": self.episode_metrics.get("min_robot_resident_distance_m"),
        }

    def ground_truth_snapshot(self):
        human_pos = _get_dummy_position(self.dummy_root).tolist() if self.dummy_root is not None else None
        return {
            "resident_zone": self.ground_truth_resident_zone(),
            "resident_position": human_pos,
            "resident_heading_deg": float(self.human_heading_deg),
            "resident_velocity": self.current_resident_velocity(),
            "activity_id": self.activity_state.activity_id,
            "posture": self.activity_state.posture,
            "virtual_sensors": dict(self.activity_state.virtual_sensors),
            "robot_pose": self.current_robot_pose(),
            "objects": self.object_state_snapshot(),
        }

    def current_resident_velocity(self):
        if not self.activity_state.movement_enabled:
            return [0.0, 0.0, 0.0]
        return [float(value) * HUMAN_MOVE_SPEED_MPS for value in self.human_input_vector]

    def object_state_snapshot(self):
        names = []
        if self.scene_profile is not None:
            names.extend(str(spec.get("name")) for spec in self.scene_profile.demo_objects if spec.get("name"))
        if self.hdf5_replay_config is not None:
            names.extend(str(name) for name in ((self.hdf5_replay_config.get("object_states") or {}).keys()))
        states = {}
        for name in sorted(set(names)):
            try:
                obj = self.env.scene.object_registry("name", name)
            except Exception:
                obj = None
            if obj is None:
                states[name] = {"available": False}
                continue
            try:
                position, orientation = obj.get_position_orientation()
                pos = [float(value) for value in position.tolist()]
                target_center = (self.task_eval.get("target") or {}).get("center") if hasattr(self, "task_eval") else None
                states[name] = {
                    "available": True,
                    "position": pos,
                    "orientation_xyzw": [float(value) for value in orientation.tolist()],
                    "held_by": None,
                    "on_floor": bool(pos[2] < 0.12),
                    "distance_to_goal_m": self.vec_distance(pos, target_center) if target_center is not None else None,
                }
            except Exception as exc:
                states[name] = {"available": False, "reason": str(exc)}
        return states

    def scene_object_by_name(self, name):
        try:
            return self.env.scene.object_registry("name", name)
        except Exception:
            return None

    def object_pose(self, name):
        obj = self.scene_object_by_name(name)
        if obj is None:
            return None, None
        try:
            position, orientation = obj.get_position_orientation()
            return [float(value) for value in position.tolist()], [float(value) for value in orientation.tolist()]
        except Exception:
            return None, None

    def object_position(self, name):
        position, _ = self.object_pose(name)
        return position

    def object_cap_point(self, name, offset_m=TASK_EVAL_CAP_OFFSET_M):
        position, orientation = self.object_pose(name)
        if position is None or orientation is None:
            return None
        try:
            offset = T.quat_apply(
                th.tensor(orientation, dtype=th.float32),
                th.tensor([0.0, 0.0, float(offset_m)], dtype=th.float32),
            )
            return [float(position[idx]) + float(offset[idx]) for idx in range(3)]
        except Exception:
            return [float(position[0]), float(position[1]), float(position[2]) + float(offset_m)]

    @staticmethod
    def vec_distance(a, b):
        if a is None or b is None:
            return None
        return math.sqrt(sum((float(a[idx]) - float(b[idx])) ** 2 for idx in range(min(len(a), len(b), 3))))

    def robot_gripper_points(self):
        points = []
        if self.robot is None:
            return points
        links = getattr(self.robot, "links", {}) or {}
        tokens = ("finger", "gripper", "eef", "wrist", "hand", "palm")
        for name, link in links.items():
            link_name = str(name).lower()
            if not any(token in link_name for token in tokens):
                continue
            try:
                position, _ = link.get_position_orientation()
                points.append(
                    {
                        "link": str(name),
                        "position": [float(value) for value in position.tolist()],
                    }
                )
            except Exception:
                continue
        if points:
            return points
        robot_pos = self.current_robot_position()
        return [] if robot_pos is None else [{"link": "robot_root_fallback", "position": robot_pos}]

    def task_eval_target_for_current_context(self):
        resident_pos = _get_dummy_position(self.dummy_root).tolist() if self.dummy_root is not None else None
        if resident_pos is not None:
            center = [float(resident_pos[0]), float(resident_pos[1]), 0.75]
            return {
                "source": "resident_context",
                "center": center,
                "half_extents": list(TASK_EVAL_TARGET_HALF_EXTENTS),
                "activity_id": self.activity_state.activity_id,
                "resident_zone": self.ground_truth_resident_zone(),
            }
        return {
            "source": "fallback_scene_center",
            "center": [0.0, 0.0, 0.75],
            "half_extents": list(TASK_EVAL_TARGET_HALF_EXTENTS),
            "activity_id": self.activity_state.activity_id,
            "resident_zone": self.ground_truth_resident_zone(),
        }

    def start_task_evaluation(self, task):
        self.task_eval = self.empty_task_eval()
        self.task_eval.update(
            {
                "enabled": True,
                "task": task,
                "replay_id": self.hdf5_replay_id,
                "label": "running",
                "started_at_wall_time_s": float(time()),
                "updated_at_wall_time_s": float(time()),
            }
        )
        self.task_eval["target"] = self.task_eval_target_for_current_context()
        obj_pos = self.object_position(TASK_EVAL_OBJECT_NAME)
        self.task_eval["object"]["position"] = obj_pos
        self.task_eval["object"]["initial_position"] = obj_pos
        self.task_eval_last_update_t = time()
        self.task_eval_last_object_pos = list(obj_pos) if obj_pos is not None else None

    def update_task_evaluation(self, now):
        if not self.task_eval.get("enabled") or self.task_eval.get("finalized"):
            return
        obj_pos = self.object_position(self.task_eval.get("object_name") or TASK_EVAL_OBJECT_NAME)
        previous_t = self.task_eval_last_update_t
        dt = max(0.0, float(now) - float(previous_t)) if previous_t is not None else 0.0
        self.task_eval_last_update_t = float(now)
        self.task_eval["updated_at_wall_time_s"] = float(now)
        if obj_pos is None:
            self.task_eval["reason"] = "object_unavailable"
            return

        cap_point = self.object_cap_point(self.task_eval.get("object_name") or TASK_EVAL_OBJECT_NAME)
        if cap_point is None:
            cap_point = [float(obj_pos[0]), float(obj_pos[1]), float(obj_pos[2]) + TASK_EVAL_CAP_OFFSET_M]
        speed = 0.0
        if self.task_eval_last_object_pos is not None and dt > 0.0:
            distance = self.vec_distance(obj_pos, self.task_eval_last_object_pos) or 0.0
            speed = distance / dt
        self.task_eval_last_object_pos = list(obj_pos)
        self.task_eval["object"].update({"position": obj_pos, "cap_point": cap_point, "speed_mps": float(speed)})

        gripper_points = self.robot_gripper_points()
        cap_distances = [
            self.vec_distance(point["position"], cap_point)
            for point in gripper_points
            if point.get("position") is not None
        ]
        cap_distances = [distance for distance in cap_distances if distance is not None]
        min_cap_distance = min(cap_distances) if cap_distances else None
        contact = bool(min_cap_distance is not None and min_cap_distance <= TASK_EVAL_GRIPPER_RADIUS_M)
        grasp = self.task_eval["subgoals"]["grasp"]
        grasp["contact"] = contact
        grasp["min_cap_distance_m"] = min_cap_distance
        grasp["hold_s"] = float(grasp.get("hold_s", 0.0) + dt) if contact else 0.0
        if grasp["hold_s"] >= TASK_EVAL_GRASP_HOLD_S:
            grasp["success"] = True

        moving = bool(speed >= TASK_EVAL_MOVE_SPEED_THRESHOLD_MPS)
        transport = self.task_eval["subgoals"]["transport"]
        transport["max_speed_mps"] = max(float(transport.get("max_speed_mps", 0.0)), float(speed))
        transport["moving_streak_s"] = float(transport.get("moving_streak_s", 0.0) + dt) if moving else 0.0
        if transport["moving_streak_s"] >= TASK_EVAL_MOVE_HOLD_S:
            transport["success"] = True

        target = self.task_eval.get("target") or {}
        center = target.get("center")
        half = target.get("half_extents") or TASK_EVAL_TARGET_HALF_EXTENTS
        inside = bool(
            center is not None
            and all(abs(float(obj_pos[idx]) - float(center[idx])) <= float(half[idx]) for idx in range(3))
        )
        stable = bool(speed < TASK_EVAL_MOVE_SPEED_THRESHOLD_MPS)
        place = self.task_eval["subgoals"]["place"]
        place["inside_target"] = inside
        place["hold_s"] = float(place.get("hold_s", 0.0) + dt) if inside and stable else 0.0
        if place["hold_s"] >= TASK_EVAL_PLACE_HOLD_S:
            place["success"] = True

        self.recompute_task_evaluation_score()

    def recompute_task_evaluation_score(self):
        subgoals = self.task_eval.get("subgoals") or {}
        score = 0.0
        for name, weight in TASK_EVAL_WEIGHTS.items():
            score += float(weight) if (subgoals.get(name) or {}).get("success") else 0.0
        self.task_eval["score"] = round(score, 4)
        self.task_eval["success"] = bool(score >= 0.999)
        if self.task_eval.get("finalized"):
            self.task_eval["label"] = "success" if self.task_eval["success"] else "partial_or_failed"
        elif score > 0.0:
            self.task_eval["label"] = "running_partial"
        else:
            self.task_eval["label"] = "running"

    def finalize_task_evaluation(self, reason):
        if not self.task_eval.get("enabled"):
            return
        self.update_task_evaluation(time())
        self.task_eval["finalized"] = True
        self.task_eval["finished_at_wall_time_s"] = float(time())
        self.task_eval["reason"] = reason
        self.recompute_task_evaluation_score()

    def finalize_task_evaluation_for_interruption(self, reason):
        if not self.task_eval.get("enabled") or self.task_eval.get("finalized"):
            return None
        self.finalize_task_evaluation(reason)
        self.episode_logger.write("task_interrupted", self.episode_payload(event_reason=reason))
        self.write_quality_report(status="task_interrupted", reason=reason)
        self.close_task_dataset_run()
        return self.task_evaluation_snapshot()

    def task_evaluation_snapshot(self):
        return json.loads(json.dumps(self.task_eval))

    def estimate_snapshot(self):
        confidence = self.resident_context.get("confidence")
        evidence = self.resident_context.get("evidence") or []
        return {
            "resident_zone": self.state.human.zone,
            "last_known_resident_zone": self.state.motion.last_known_zone,
            "resident_context": dict(self.resident_context),
            "confidence": confidence,
            "evidence": list(evidence),
        }

    def sensor_quality_snapshot(self):
        ground_truth_zone = self.ground_truth_resident_zone()
        estimated_zone = self.state.human.zone
        motion_detected = bool(self.state.motion.detected)
        motion_dropout = ground_truth_zone not in {None, "unknown", "configured_start"} and not motion_detected
        zone_mismatch = (
            ground_truth_zone not in {None, "unknown", "configured_start"}
            and estimated_zone not in {None, "unknown"}
            and estimated_zone != ground_truth_zone
        )
        faults = []
        if motion_dropout:
            faults.append("motion_dropout")
        if zone_mismatch:
            faults.append("zone_mismatch")
        return {
            "motion_detected": motion_detected,
            "motion_dropout": bool(motion_dropout),
            "zone_mismatch": bool(zone_mismatch),
            "virtual_sensor_count": len(self.activity_state.virtual_sensors),
            "has_activity_context": bool(self.activity_state.context),
            "sensor_faults": faults,
        }

    def training_validity_snapshot(self):
        task_eval_enabled = bool(self.task_eval.get("enabled"))
        task_eval_finalized = bool(self.task_eval.get("finalized"))
        return {
            "context_model": True,
            "task_selection": True,
            "safety_eval": True,
            "policy_behavior_cloning": bool(self.hdf5_replay_actions is not None),
            "policy_rollout_eval": task_eval_enabled,
            "policy_invalid_reason": None if task_eval_enabled else "no_task_outcome_label",
            "task_outcome_eval": {
                "enabled": task_eval_enabled,
                "finalized": task_eval_finalized,
                "score": self.task_eval.get("score"),
                "success": self.task_eval.get("success"),
            },
            "usable_for": [
                "context_baseline",
                "task_selection",
                "safety_eval",
                "behavior_cloning",
                "task_outcome_eval",
            ]
            if task_eval_enabled
            else ["context_baseline", "task_selection", "safety_eval"],
        }

    def gym_framework_metadata(self, task=None, replay_id=None, policy_source=None):
        scene_model = self.scene_profile.scene_model if self.scene_profile else (self.args.scene_model or PRESETS[self.args.preset]["scene"])
        scene_variant = self.active_scene_variant or "profile"
        env_suffix = "v09" if scene_model == "Merom_0_int" else self.safe_camera_name(scene_variant, "default")
        task_name = str(task or self.state.robot.task or "unknown_task")
        task_id = "medicine_delivery" if task_name in {"deliver_item", "medicine_delivery"} else self.safe_camera_name(task_name)
        scenario_id = self.activity_state.activity_id or self.episode_scenario_type or self.infer_scenario_type()
        return {
            "framework_id": "homesense_gym",
            "schema_version": "homesense_gym_v1",
            "registry_path": "smart_home/configs/gym_registry.yaml",
            "env_id": f"{self.safe_camera_name(scene_model).lower()}_{env_suffix}",
            "scene_model": scene_model,
            "scene_variant": scene_variant,
            "robot_id": self.safe_camera_name(self.args.robot_type or "r1pro").lower(),
            "task_id": task_id,
            "task_name": task_name,
            "scenario_id": self.safe_camera_name(scenario_id or "unspecified").lower(),
            "resident_zone_id": self.safe_camera_name(self.ground_truth_resident_zone()).lower(),
            "sensor_layout_id": self.safe_camera_name(self.sensor_layout or "current").lower(),
            "policy_source": policy_source
            or ("teleoperation_replay" if (replay_id or self.hdf5_replay_id) else "manual_or_scripted"),
            "replay_id": replay_id or self.hdf5_replay_id,
            "evaluator_id": "medicine_delivery_v1" if task_id == "medicine_delivery" else f"{task_id}_v1",
            "dataset_format": "homesense_task_run_v1",
            "future_integrations": {
                "nvidia_cosmos": {
                    "status": "todo",
                    "interface": "video_action_context_label_export",
                    "intended_use": "world_model_rollout_prediction_and_synthetic_variation_generation",
                }
            },
        }

    def update_episode_metrics(self):
        self.episode_metrics["frame_count"] += 1
        self.update_task_evaluation(time())
        distance = self.current_robot_resident_distance()
        if distance is None:
            return
        previous = self.episode_metrics["min_robot_resident_distance_m"]
        if previous is None or distance < float(previous):
            self.episode_metrics["min_robot_resident_distance_m"] = distance

    def camera_missing_frame_ratio(self):
        if not self.camera_log_enabled:
            return None
        saved = sum(int(self.camera_frame_counts.get(source, 0)) for source in self.camera_log_sources)
        missing = sum(int(self.camera_frame_missing_counts.get(source, 0)) for source in self.camera_log_sources)
        total = saved + missing
        if total <= 0:
            return None
        return missing / total

    def episode_phase(self):
        if self.robot_replay_active or self.state.robot.status == "running_replay":
            return "task_running"
        if self.episode_metrics.get("task_completed"):
            return "task_finished"
        if self.episode_metrics.get("task_started"):
            return "task_post"
        return "context_init"

    def episode_payload(self, event_reason=None):
        data = self.snapshot()
        return {
            "episode_id": self.episode_id,
            "episode_seed": self.episode_seed,
            "scene_model": self.scene_profile.scene_model if self.scene_profile else (self.args.scene_model or PRESETS[self.args.preset]["scene"]),
            "resident_zone_requested": self.args.resident_zone,
            "resident_zone_sampled": self.episode_zone,
            "started_at": self.episode_started_at,
            "reason": event_reason,
            "task": data.get("robot", {}).get("task"),
            "robot_status": data.get("robot", {}).get("status"),
            "scenario_type": data.get("scenario_type"),
            "resident": data.get("human"),
            "motion": data.get("motion"),
            "pressure": data.get("pressure"),
            "activity_context": data.get("activity_context"),
            "robot_context": data.get("robot", {}).get("context"),
            "ground_truth": data.get("ground_truth"),
            "estimates": data.get("estimates"),
            "sensor_quality": data.get("sensor_quality"),
            "risk": data.get("risk"),
            "task_evaluation": data.get("task_evaluation"),
            "training_validity": data.get("training_validity"),
            "episode_phase": self.episode_phase(),
            "metrics": dict(self.episode_metrics),
        }

    def finish_episode(self, reason):
        if self.episode_id <= 0:
            return
        self.episode_logger.write("episode_end", self.episode_payload(event_reason=reason))
        self.write_quality_report(status="closed", reason=reason)

    def write_quality_report(self, status="open", reason=None):
        self.episode_logger.write_quality_report(
            {
                "status": status,
                "last_episode_id": self.episode_id,
                "episode_count": self.episode_id,
                "step_count": int(self.episode_metrics.get("frame_count") or 0),
                "last_reason": reason,
                "min_robot_resident_distance_m": self.episode_metrics.get("min_robot_resident_distance_m"),
                "task_started": bool(self.episode_metrics.get("task_started")),
                "task_completed": bool(self.episode_metrics.get("task_completed")),
                "task_blocked": bool(self.episode_metrics.get("task_blocked")),
                "last_task": self.episode_metrics.get("last_task"),
                "last_replay_id": self.episode_metrics.get("last_replay_id"),
                "task_evaluation": self.task_evaluation_snapshot(),
                "camera_frame_counts": dict(self.camera_frame_counts),
                "camera_frame_missing_counts": dict(self.camera_frame_missing_counts),
                "missing_frame_ratio": self.camera_missing_frame_ratio(),
                "missing_state_ratio": None,
            }
        )

    def scenario_metadata_snapshot(self):
        return {
            "episode_id": self.episode_id,
            "episode_seed": self.episode_seed,
            "episode_zone": self.episode_zone,
            "scenario_type": self.episode_scenario_type or self.infer_scenario_type(),
            "resident": {
                "zone": self.ground_truth_resident_zone(),
                "position": list(self.human_target_pos) if self.human_target_pos is not None else None,
                "heading_deg": float(self.human_heading_deg),
                "posture": self.activity_state.posture,
                "movement_enabled": bool(self.activity_state.movement_enabled),
            },
            "activity_context": {
                "activity_id": self.activity_state.activity_id,
                "ground_truth_zone": self.activity_state.ground_truth_zone,
                "spawn_points": list(self.activity_state.spawn_points or []),
                "virtual_sensors": dict(self.activity_state.virtual_sensors),
                "estimated_context": dict(self.resident_context),
            },
            "sensors": {
                "layout": self.sensor_layout,
                "active_motion_sensors": list(getattr(self.sensor_rig, "active_motion_sensor_names", []))
                if self.sensor_rig is not None
                else [],
                "initial_readings": self.current_readings,
            },
        }

    def begin_task_dataset_run(self, task, replay_id=None, selection=None, reset_snapshot=None):
        self.reset_task_run_counters()
        replay_id = replay_id or self.hdf5_replay_id
        scenario = self.episode_scenario_type or self.infer_scenario_type()
        run_dir = self.episode_logger.begin_run(
            metadata={
                "schema_version": "homesense_task_run_metadata_v1",
                "gym_framework": self.gym_framework_metadata(
                    task=task,
                    replay_id=replay_id,
                    policy_source="teleoperation_replay" if replay_id else "manual_or_scripted",
                ),
                "task": {
                    "name": task,
                    "replay_id": replay_id,
                    "selection": selection,
                    "reset_snapshot": reset_snapshot,
                    "playback_mode": self.hdf5_replay_playback_mode,
                },
                "scenario": self.scenario_metadata_snapshot(),
                "files": {
                    "events": "metadata/events.jsonl",
                    "steps": "data/steps.jsonl",
                    "annotations": "metadata/annotations.json",
                    "quality_report": "metadata/quality_report.json",
                    "hdf5": "metadata/dataset.hdf5",
                    "camera_dir": "data/cameras/<robot_camera_name>/",
                },
            },
            name_parts=[
                f"ep{self.episode_id:04d}",
                scenario,
                replay_id,
            ],
        )
        if run_dir is not None:
            bridge.log(
                "dataset_task_run_started",
                {
                    "run_dir": str(run_dir),
                    "episode_id": self.episode_id,
                    "scenario_type": scenario,
                    "task": task,
                    "replay_id": replay_id,
                },
            )
        return run_dir

    def close_task_dataset_run(self):
        current_run_dir = self.episode_logger.run_dir
        self.episode_logger.end_run()
        if current_run_dir is not None:
            bridge.log("dataset_task_run_closed", {"run_dir": str(current_run_dir)})

    def start_episode(self, zone="random", reason="manual", randomize=True):
        if self.state.robot.busy:
            return {"event": "new_episode_blocked", "reason": "robot task is running"}
        if self.episode_id > 0:
            self.finish_episode(reason=f"superseded_by_{reason}")
        self.clear_viewport_input()
        self.human_input_vector = (0.0, 0.0, 0.0)
        reset_snapshot = {
            "event": "runtime_initial_state_deferred",
            "reason": f"episode_start:{reason}",
            "deferred_to": "replay_start",
        }
        self.state.robot.status = "idle"
        self.state.robot.task = None
        self.state.robot.replay_id = None
        self.robot_task_end_t = None
        self.robot_replay_active = False
        self.robot_replay_paused = False
        self.robot_replay_step = 0
        self.episode_id += 1
        self.episode_started_at = utc_now_iso()
        self.episode_scenario_type = None
        self.episode_metrics = self.empty_episode_metrics()
        self.task_eval = self.empty_task_eval()
        self.task_eval_last_update_t = None
        self.task_eval_last_object_pos = None
        if randomize:
            self.episode_zone = self.choose_episode_zone(zone)
            self.activity_state = self.activity_simulator.start_episode(self.episode_zone, self.rng)
            resident_pos = self.sample_resident_position_for_zone(
                self.episode_zone,
                activity_spawn_points=self.activity_state.spawn_points,
                collision_check=self.activity_state.spawn_collision_check,
            )
        else:
            self.episode_zone = "configured_start"
            self.activity_state = self.activity_simulator.start_episode(self.episode_zone, self.rng)
            resident_pos = self.human_start_position(PRESETS[self.args.preset])
        if self.activity_state.spawn_collision_check:
            self.human_target_pos = self.find_nearest_free_position(resident_pos)
        else:
            self.human_target_pos = resident_pos
        self.human_heading_deg = float(self.activity_state.heading_deg) if self.activity_state.heading_deg is not None else 0.0
        self._set_human_pose(self.human_target_pos, self.human_heading_deg)
        self._set_human_visual_posture(self.activity_state.posture)
        self.set_camera("overview")
        self.read_sensors()
        self.episode_scenario_type = self.infer_scenario_type()
        payload = self.episode_payload(event_reason=reason)
        payload["sampled_position"] = self.human_target_pos
        self.episode_logger.write("episode_start", payload)
        bridge.log(
            "episode_started",
            {
                "episode_id": self.episode_id,
                "zone": self.episode_zone,
                "position": self.human_target_pos,
                "reset_snapshot": reset_snapshot,
                "log_path": str(self.episode_logger.path) if self.episode_logger.path else None,
            },
        )
        return {
            "event": "episode_started",
            "episode_id": self.episode_id,
            "zone": self.episode_zone,
            "position": self.human_target_pos,
            "reset_snapshot": reset_snapshot,
        }

    def update_human_motion(self):
        now = time()
        dt = min(now - self.human_last_move_t, 0.05)
        self.human_last_move_t = now
        pos = _get_dummy_position(self.dummy_root)
        input_vec = th.tensor(self.human_input_vector, dtype=th.float32)
        input_len = float(th.norm(input_vec))
        if input_len > 1e-5:
            direction = input_vec / input_len
            step = HUMAN_MOVE_SPEED_MPS * dt
            next_pos = (pos + direction * step).tolist()
            blocked_by = self._movement_blocker(next_pos)
            if blocked_by is not None:
                self.human_target_pos = pos.tolist()
                return
            self.human_target_pos = next_pos
            self._set_human_pose(next_pos, self.human_heading_deg)
            return

        if self.human_target_pos is None:
            return
        target = th.tensor(self.human_target_pos, dtype=th.float32)
        delta = target - pos
        distance = float(th.norm(delta))
        if distance < 1e-4:
            return
        step = min(distance, HUMAN_MOVE_SPEED_MPS * dt)
        next_pos = (pos + delta / distance * step).tolist()
        self._set_human_pose(next_pos, self.human_heading_deg)

    def _set_human_pose(self, position, heading_deg=None):
        def apply_pose():
            _set_dummy_position(self.dummy_root, position)
            if heading_deg is not None:
                attr = self.dummy_root.GetPrim().GetAttribute("xformOp:rotateZ")
                if attr:
                    attr.Set(float(heading_deg))

        if getattr(og.sim, "_editing_usd", False):
            apply_pose()
            return
        with og.sim.editing_usd():
            apply_pose()

    def _set_human_visual_posture(self, posture):
        if self.dummy_root is None or posture == self.human_posture:
            return

        pxr = lazy.pxr
        stage = og.sim.stage

        def set_translate(name, xyz):
            prim = stage.GetPrimAtPath(f"/World/dummy_human/{name}")
            if prim and prim.IsValid():
                attr = prim.GetAttribute("xformOp:translate")
                if attr:
                    attr.Set(pxr.Gf.Vec3d(*xyz))

        def set_rotate(name, axis, deg):
            prim = stage.GetPrimAtPath(f"/World/dummy_human/{name}")
            if not prim or not prim.IsValid():
                return
            axis = str(axis).upper()
            attr = prim.GetAttribute(f"xformOp:rotate{axis}")
            if attr:
                attr.Set(float(deg))
                return
            try:
                xform = pxr.UsdGeom.Xformable(prim)
                if axis == "X":
                    xform.AddRotateXOp().Set(float(deg))
                elif axis == "Y":
                    xform.AddRotateYOp().Set(float(deg))
                elif axis == "Z":
                    xform.AddRotateZOp().Set(float(deg))
            except Exception:
                pass

        def set_rotate_x(name, deg):
            set_rotate(name, "X", deg)

        def set_rotate_y(name, deg):
            set_rotate(name, "Y", deg)

        def reset_part_rotations():
            for part in ("torso", "head", "hair", "left_leg", "right_leg", "left_arm", "right_arm", "left_hand", "right_hand", "left_shoe", "right_shoe"):
                set_rotate_x(part, 0.0)
                set_rotate_y(part, 0.0)

        def apply_posture():
            if posture == "seated":
                reset_part_rotations()
                set_translate("torso", (0.0, 0.0, 0.78))
                set_translate("head", (0.0, 0.0, 1.16))
                set_translate("hair", (0.0, -0.02, 1.28))
                set_translate("left_leg", (-0.07, 0.18, 0.42))
                set_translate("right_leg", (0.07, 0.18, 0.42))
                set_rotate_x("left_leg", 82.0)
                set_rotate_x("right_leg", 82.0)
                set_translate("left_arm", (-0.22, 0.02, 0.76))
                set_translate("right_arm", (0.22, 0.02, 0.76))
                set_translate("left_hand", (-0.23, 0.14, 0.58))
                set_translate("right_hand", (0.23, 0.14, 0.58))
                set_translate("left_shoe", (-0.07, 0.50, 0.24))
                set_translate("right_shoe", (0.07, 0.50, 0.24))
                set_rotate_x("left_shoe", 82.0)
                set_rotate_x("right_shoe", 82.0)
            elif posture == "bending":
                reset_part_rotations()
                set_translate("torso", (0.0, 0.16, 0.86))
                set_rotate_x("torso", 54.0)
                set_translate("head", (0.0, 0.46, 1.06))
                set_rotate_x("head", 38.0)
                set_translate("hair", (0.0, 0.42, 1.18))
                set_rotate_x("hair", 38.0)
                set_translate("left_leg", (-0.07, 0.0, 0.36))
                set_translate("right_leg", (0.07, 0.0, 0.36))
                set_translate("left_arm", (-0.22, 0.36, 0.74))
                set_translate("right_arm", (0.22, 0.36, 0.74))
                set_rotate_x("left_arm", 62.0)
                set_rotate_x("right_arm", 62.0)
                set_translate("left_hand", (-0.22, 0.58, 0.48))
                set_translate("right_hand", (0.22, 0.58, 0.48))
                set_translate("left_shoe", (-0.07, 0.10, 0.04))
                set_translate("right_shoe", (0.07, 0.10, 0.04))
            elif posture == "lying":
                reset_part_rotations()
                set_translate("torso", (0.0, 0.0, 0.58))
                set_rotate_x("torso", 90.0)
                set_translate("head", (0.0, 0.48, 0.58))
                set_rotate_x("head", 90.0)
                set_translate("hair", (0.0, 0.58, 0.63))
                set_rotate_x("hair", 90.0)
                set_translate("left_leg", (-0.07, -0.52, 0.55))
                set_translate("right_leg", (0.07, -0.52, 0.55))
                set_rotate_x("left_leg", 90.0)
                set_rotate_x("right_leg", 90.0)
                set_translate("left_arm", (-0.34, -0.10, 0.58))
                set_translate("right_arm", (0.34, -0.10, 0.58))
                set_rotate_x("left_arm", 90.0)
                set_rotate_x("right_arm", 90.0)
                set_translate("left_hand", (-0.36, -0.42, 0.56))
                set_translate("right_hand", (0.36, -0.42, 0.56))
                set_translate("left_shoe", (-0.07, -0.94, 0.54))
                set_translate("right_shoe", (0.07, -0.94, 0.54))
                set_rotate_x("left_shoe", 90.0)
                set_rotate_x("right_shoe", 90.0)
            elif posture == "piano_reaching":
                reset_part_rotations()
                set_translate("torso", (0.0, 0.04, 0.76))
                set_rotate_x("torso", 12.0)
                set_translate("head", (0.0, 0.09, 1.17))
                set_rotate_x("head", 8.0)
                set_translate("hair", (0.0, 0.06, 1.30))
                set_rotate_x("hair", 8.0)
                set_translate("left_leg", (-0.08, 0.18, 0.40))
                set_translate("right_leg", (0.08, 0.18, 0.40))
                set_rotate_x("left_leg", 82.0)
                set_rotate_x("right_leg", 82.0)
                set_translate("left_arm", (-0.22, 0.26, 0.82))
                set_translate("right_arm", (0.22, 0.26, 0.82))
                set_rotate_x("left_arm", 78.0)
                set_rotate_x("right_arm", 78.0)
                set_translate("left_hand", (-0.18, 0.56, 0.72))
                set_translate("right_hand", (0.18, 0.56, 0.72))
                set_translate("left_shoe", (-0.08, 0.50, 0.22))
                set_translate("right_shoe", (0.08, 0.50, 0.22))
                set_rotate_x("left_shoe", 82.0)
                set_rotate_x("right_shoe", 82.0)
            else:
                # Recreate the procedural standing layout used by add_demo_human_avatar(height=1.7).
                reset_part_rotations()
                height = 1.7
                leg_h = height * 0.40
                torso_h = height * 0.38
                shoulder_z = leg_h + torso_h * 0.72
                torso_radius = height * 0.105
                limb_radius = height * 0.032
                head_radius = height * 0.088
                set_translate("torso", (0.0, 0.0, leg_h + torso_h * 0.48))
                set_translate("head", (0.0, 0.0, leg_h + torso_h + head_radius * 1.15))
                set_translate("hair", (0.0, -head_radius * 0.14, leg_h + torso_h + head_radius * 1.82))
                set_translate("left_leg", (-torso_radius * 0.42, 0.0, leg_h * 0.5))
                set_translate("right_leg", (torso_radius * 0.42, 0.0, leg_h * 0.5))
                set_rotate_x("left_leg", 0.0)
                set_rotate_x("right_leg", 0.0)
                set_translate("left_arm", (-torso_radius * 1.28, 0.0, shoulder_z))
                set_translate("right_arm", (torso_radius * 1.28, 0.0, shoulder_z))
                set_translate("left_hand", (-torso_radius * 1.30, 0.04, shoulder_z - torso_h * 0.40))
                set_translate("right_hand", (torso_radius * 1.30, 0.04, shoulder_z - torso_h * 0.40))
                set_translate("left_shoe", (-torso_radius * 0.42, 0.08, 0.04))
                set_translate("right_shoe", (torso_radius * 0.42, 0.08, 0.04))
                set_rotate_x("left_shoe", 0.0)
                set_rotate_x("right_shoe", 0.0)

        with og.sim.editing_usd():
            apply_posture()
        self.human_posture = posture

    def human_forward_from_heading(self, heading_deg=None):
        heading = math.radians(self.human_heading_deg if heading_deg is None else heading_deg)
        return [-math.sin(heading), math.cos(heading)]

    def heading_from_world_vector(self, dx, dy):
        return math.degrees(math.atan2(-float(dx), float(dy)))

    def _overview_orientation(self, position, look_at):
        camera_pos = th.tensor(position, dtype=th.float32)
        target_pos = th.tensor(look_at, dtype=th.float32)
        forward = target_pos - camera_pos
        forward = forward / th.norm(forward)
        right = th.tensor([0.0, -1.0, 0.0], dtype=th.float32)
        up = th.tensor([1.0, 0.0, 0.0], dtype=th.float32)
        rot = th.stack([right, up, -forward], dim=1)
        return T.mat2quat(rot)

    def _viewer_camera_pose(self, mode):
        if mode not in CAMERA_PRESETS:
            mode = "overview"
        if mode == "resident":
            human = _get_dummy_position(self.dummy_root).tolist()
            forward = self.human_forward_from_heading()
            position = [human[0] - forward[0] * 1.7, human[1] - forward[1] * 1.7, human[2] + 1.45]
            look_at = [human[0], human[1], human[2] + 1.1]
        elif mode == "overview" and self.scene_bounds is not None:
            overview = self.scene_profile.overview_camera if self.scene_profile is not None else None
            if overview and not overview.get("auto_from_scene_bounds", False):
                position = overview["position"]
                look_at = overview["look_at"]
            else:
                center = self.scene_bounds["center"]
                size = self.scene_bounds["size"]
                height = max(10.0, min(18.0, max(size[0], size[1]) * 1.32))
                position = [center[0], center[1], height]
                look_at = [center[0], center[1], 0.0]
        elif mode == "robot" and self.robot is not None:
            try:
                robot_pos_tensor, robot_quat = self.robot.get_position_orientation()
                robot_pos = robot_pos_tensor.tolist()
                forward_tensor = T.quat_apply(robot_quat, th.tensor([1.0, 0.0, 0.0], dtype=th.float32))
                forward = forward_tensor[:2].tolist()
                forward_norm = math.hypot(forward[0], forward[1])
                if forward_norm < 1e-4:
                    forward = [0.0, 1.0]
                else:
                    forward = [forward[0] / forward_norm, forward[1] / forward_norm]
            except Exception:
                robot_pos = None
            if robot_pos is not None:
                distance = 2.8 if self.robot_replay_active else 2.35
                target_ahead = 1.15 if self.robot_replay_active else 0.8
                position = [robot_pos[0] - forward[0] * distance, robot_pos[1] - forward[1] * distance, robot_pos[2] + 1.25]
                look_at = [robot_pos[0] + forward[0] * target_ahead, robot_pos[1] + forward[1] * target_ahead, robot_pos[2] + 0.68]
            else:
                preset = CAMERA_PRESETS[mode]
                position = preset["position"]
                look_at = preset["look_at"]
        else:
            preset = CAMERA_PRESETS[mode]
            position = preset["position"]
            look_at = preset["look_at"]
        return mode, position, look_at

    def _apply_viewer_camera_pose(self, mode, position, look_at):
        orientation = self._overview_orientation(position, look_at) if mode == "overview" else _look_at_quat(position, look_at)
        og.sim.viewer_camera.set_position_orientation(
            position=th.tensor(position, dtype=th.float32),
            orientation=orientation,
        )

    def capture_default_viewer_camera_pose(self):
        try:
            position, orientation = og.sim.viewer_camera.get_position_orientation()
            self.default_viewer_camera_pose = {
                "position": [float(v) for v in position.tolist()],
                "orientation_xyzw": [float(v) for v in orientation.tolist()],
            }
            bridge.log("default_viewer_camera_captured", self.default_viewer_camera_pose)
        except Exception as exc:
            self.default_viewer_camera_pose = None
            bridge.log("default_viewer_camera_capture_failed", {"reason": str(exc)})

    def enable_default_viewer_camera_controls(self):
        try:
            self.viewer_camera_mover = og.sim.enable_viewer_camera_teleoperation()
            self.viewer_camera_mover.set_delta(float(getattr(self.args, "camera_speed", 0.08)))
            bridge.log("default_viewer_camera_controls_enabled", {"camera_speed": float(getattr(self.args, "camera_speed", 0.08))})
        except Exception as exc:
            self.viewer_camera_mover = None
            bridge.log("default_viewer_camera_controls_failed", {"reason": str(exc)})

    def restore_default_viewer_camera(self):
        if self.default_viewer_camera_pose is None:
            bridge.log("default_viewer_camera_restore_skipped", {"reason": "no captured default viewer camera pose"})
            return None
        pose = self.default_viewer_camera_pose
        og.sim.viewer_camera.set_position_orientation(
            position=th.tensor(pose["position"], dtype=th.float32),
            orientation=th.tensor(pose["orientation_xyzw"], dtype=th.float32),
        )
        bridge.log("default_viewer_camera_restored", {"reason": "hdf5_replay_robot_camera", **pose})
        return pose

    def set_camera(self, mode):
        if mode == "free":
            pose = self.restore_default_viewer_camera()
            self.state.camera_mode = mode
            self.mark_ceiling_visibility_dirty()
            return {
                "mode": mode,
                "camera": "omnigibson_default_viewer_free",
                "position": None if pose is None else pose["position"],
                "orientation_xyzw": None if pose is None else pose["orientation_xyzw"],
            }
        mode, position, look_at = self._viewer_camera_pose(mode)
        self._apply_viewer_camera_pose(mode, position, look_at)
        self.state.camera_mode = mode
        self.mark_ceiling_visibility_dirty()
        return {"mode": mode, "position": position, "look_at": look_at}

    def update_follow_camera(self, now):
        if self.video_source != "viewer" or self.state.camera_mode != "resident":
            return
        if now - self.last_follow_camera_t < self.follow_camera_interval_s:
            return
        self.last_follow_camera_t = now
        mode, position, look_at = self._viewer_camera_pose(self.state.camera_mode)
        self._apply_viewer_camera_pose(mode, position, look_at)

    def select_hdf5_replay_for_current_context(self):
        if not self.hdf5_replay_paths:
            return {"event": "hdf5_replay_select_skipped", "reason": "no_hdf5_replays"}
        activity_id = self.activity_state.activity_id
        target_key = SCENARIO_REPLAY_RULES.get(activity_id)
        if target_key is None:
            return {
                "event": "hdf5_replay_select_skipped",
                "reason": "no_activity_mapping",
                "activity_id": activity_id,
                "current_replay_id": self.hdf5_replay_id,
            }
        selected_path = None
        for path in self.hdf5_replay_paths:
            if self.replay_key_for_path(path) == target_key or Path(path).stem == target_key:
                selected_path = path
                break
        if selected_path is None:
            return {
                "event": "hdf5_replay_select_failed",
                "activity_id": activity_id,
                "target_replay_key": target_key,
                "available_replays": [path.stem for path in self.hdf5_replay_paths],
            }
        if self.hdf5_replay_config is not None and Path(self.hdf5_replay_config.get("path", "")).resolve() == Path(selected_path).resolve():
            return {
                "event": "hdf5_replay_selected",
                "activity_id": activity_id,
                "replay_id": self.hdf5_replay_id,
                "reason": "already_loaded",
            }
        self.load_hdf5_replay(selected_path, reason=f"activity:{activity_id}")
        return {
            "event": "hdf5_replay_selected",
            "activity_id": activity_id,
            "replay_id": self.hdf5_replay_id,
            "path": str(selected_path),
            "reason": "activity_mapping",
        }

    def toggle_replay_pause(self):
        if not self.robot_replay_active:
            return {"event": "hdf5_replay_pause_ignored", "reason": "replay_not_running"}
        self.robot_replay_paused = not self.robot_replay_paused
        return {
            "event": "hdf5_replay_paused" if self.robot_replay_paused else "hdf5_replay_resumed",
            "replay_id": self.hdf5_replay_id,
            "step": int(self.robot_replay_step),
        }

    def run_task(self, task):
        if self.state.robot.busy:
            return {"event": "task_blocked", "task": task, "reason": "robot already busy"}
        self.clear_viewport_input()
        self.human_input_vector = (0.0, 0.0, 0.0)
        self.human_target_pos = _get_dummy_position(self.dummy_root).tolist()
        if task == "deliver_item" and self.hdf5_replay_actions is not None:
            selection = self.select_hdf5_replay_for_current_context()
            bridge.log(selection.get("event", "hdf5_replay_selected"), selection)
            if selection.get("event") != "hdf5_replay_selected":
                self.episode_metrics["task_blocked"] = True
                self.episode_logger.write("task_blocked", self.episode_payload(event_reason=selection.get("reason", "no_matching_hdf5_replay")))
                return {
                    "event": "task_blocked",
                    "task": task,
                    "reason": "no_matching_hdf5_replay_for_activity",
                    "selection": selection,
                }
            reset_snapshot = self.restore_runtime_initial_state(reason="replay_start")
            self.state.robot.status = "running_replay"
            self.state.robot.task = task
            self.state.robot.replay_id = self.hdf5_replay_id
            self.robot_replay_active = True
            self.robot_replay_paused = False
            self.robot_replay_step = 0
            self.robot_task_end_t = None
            run_dir = self.begin_task_dataset_run(
                task,
                replay_id=self.hdf5_replay_id,
                selection=selection,
                reset_snapshot=reset_snapshot,
            )
            self.episode_metrics["task_started"] = True
            self.episode_metrics["last_task"] = task
            self.episode_metrics["last_replay_id"] = self.hdf5_replay_id
            self.start_task_evaluation(task)
            self.episode_logger.write("task_started", self.episode_payload(event_reason="hdf5_action_replay_started"))
            bridge.log(
                "hdf5_action_replay_started",
                {
                    "task": task,
                    "replay_id": self.hdf5_replay_id,
                    "num_actions": int(self.hdf5_replay_actions.shape[0]),
                    "playback_mode": self.hdf5_replay_playback_mode,
                    "selection": selection,
                    "camera_mode": self.state.camera_mode,
                    "reset_snapshot": reset_snapshot,
                    "dataset_run_dir": str(run_dir) if run_dir is not None else None,
                },
            )
            return {
                "event": "hdf5_action_replay_started",
                "task": task,
                "replay_id": self.hdf5_replay_id,
                "num_actions": int(self.hdf5_replay_actions.shape[0]),
                "playback_mode": self.hdf5_replay_playback_mode,
                "selection": selection,
                "reset_snapshot": reset_snapshot,
                "dataset_run_dir": str(run_dir) if run_dir is not None else None,
            }
        try:
            replay = self.registry.select(TaskCommand(task=task), self.state)
        except ReplaySelectionError as exc:
            self.state.robot.status = "blocked"
            self.state.robot.task = task
            self.state.robot.replay_id = None
            self.episode_metrics["task_blocked"] = True
            self.episode_metrics["last_task"] = task
            self.episode_logger.write("task_blocked", self.episode_payload(event_reason=str(exc)))
            return {"event": "task_blocked", "task": task, "reason": str(exc)}
        self.state.robot.status = "running_replay"
        self.state.robot.task = task
        self.state.robot.replay_id = replay.replay_id
        self.begin_task_dataset_run(task, replay_id=replay.replay_id)
        self.episode_metrics["task_started"] = True
        self.episode_metrics["last_task"] = task
        self.episode_metrics["last_replay_id"] = replay.replay_id
        self.start_task_evaluation(task)
        self.robot_task_end_t = time() + self.args.task_duration_s
        self.episode_logger.write("task_started", self.episode_payload(event_reason="replay_started"))
        return {"event": "replay_started", "task": task, "replay_id": replay.replay_id, "context": self.state.robot.context}

    def reset_scene(self):
        if self.args.randomize_resident_on_reset:
            return self.start_episode(zone=self.args.resident_zone, reason="reset", randomize=True)
        preset = PRESETS[self.args.preset]
        human_start_pos = self.human_start_position(preset)
        self.clear_viewport_input()
        self.human_input_vector = (0.0, 0.0, 0.0)
        reset_snapshot = self.restore_runtime_initial_state(reason="scene_reset")
        self.human_target_pos = self.find_nearest_free_position(human_start_pos)
        self.human_heading_deg = 0.0
        self._set_human_pose(self.human_target_pos, self.human_heading_deg)
        self._set_human_visual_posture("standing")
        self.state.robot.status = "idle"
        self.state.robot.task = None
        self.state.robot.replay_id = None
        self.robot_task_end_t = None
        self.robot_replay_active = False
        self.robot_replay_paused = False
        self.robot_replay_step = 0
        self.set_camera("overview")
        self.read_sensors()
        return {"position": self.human_target_pos, "reset_snapshot": reset_snapshot}

    def update_task(self):
        if self.robot_replay_active and self.hdf5_replay_actions is not None:
            if self.robot_replay_step >= len(self.hdf5_replay_actions):
                self.state.robot.status = "completed"
                self.robot_replay_active = False
                self.robot_replay_paused = False
                self.episode_metrics["task_completed"] = True
                self.finalize_task_evaluation("hdf5_action_replay_finished")
                self.episode_logger.write("task_completed", self.episode_payload(event_reason="hdf5_action_replay_finished"))
                self.write_quality_report(status="task_completed", reason="hdf5_action_replay_finished")
                self.close_task_dataset_run()
                bridge.log(
                    "hdf5_action_replay_finished",
                    {
                        "replay_id": self.hdf5_replay_id,
                        "steps": int(self.robot_replay_step),
                        "task_evaluation": self.task_evaluation_snapshot(),
                    },
                )
            return
        if self.robot_task_end_t is not None and time() >= self.robot_task_end_t:
            self.state.robot.status = "completed"
            self.robot_task_end_t = None
            self.episode_metrics["task_completed"] = True
            self.finalize_task_evaluation("task_duration_elapsed")
            self.episode_logger.write("task_completed", self.episode_payload(event_reason="task_duration_elapsed"))
            self.write_quality_report(status="task_completed", reason="task_duration_elapsed")
            self.close_task_dataset_run()

    def next_robot_action(self):
        if not self.robot_replay_active or self.hdf5_replay_actions is None:
            self.last_robot_action_record = {
                "source": "zero",
                "step": None,
                "vector": None,
                "controller": None,
                "normalized": None,
            }
            return self.zero_action
        if self.robot_replay_step >= len(self.hdf5_replay_actions):
            self.last_robot_action_record = {
                "source": "zero",
                "step": int(self.robot_replay_step),
                "vector": None,
                "controller": None,
                "normalized": None,
            }
            return self.zero_action
        if self.robot_replay_paused:
            self.last_robot_action_record = {
                "source": "paused",
                "replay_id": self.hdf5_replay_id,
                "step": int(self.robot_replay_step),
                "total_steps": int(len(self.hdf5_replay_actions)),
                "vector": None,
                "controller": self.hdf5_controller_summary(),
                "normalized": bool((self.hdf5_replay_config or {}).get("robot_config", {}).get("action_normalize", False)),
                "playback_mode": self.hdf5_replay_playback_mode,
            }
            return self.zero_action
        if self.hdf5_replay_playback_mode == "state" and not self.hdf5_state_replay_failed:
            try:
                state = self.hdf5_replay_states[self.robot_replay_step]
                state_size = int(self.hdf5_replay_state_sizes[self.robot_replay_step])
                og.sim.load_state(state[:state_size], serialized=True)
            except Exception as exc:
                self.hdf5_state_replay_failed = True
                self.robot_replay_active = False
                self.robot_replay_paused = False
                self.state.robot.status = "blocked"
                bridge.log(
                    "hdf5_state_replay_failed",
                    {
                        "replay_id": self.hdf5_replay_id,
                        "step": int(self.robot_replay_step),
                        "reason": str(exc),
                        "fallback": None,
                        "scene_file": None if self.active_scene_file is None else str(self.active_scene_file),
                    },
                )
                self.last_robot_action_record = {
                    "source": "blocked",
                    "replay_id": self.hdf5_replay_id,
                    "step": int(self.robot_replay_step),
                    "vector": None,
                    "controller": self.hdf5_controller_summary(),
                    "normalized": bool((self.hdf5_replay_config or {}).get("robot_config", {}).get("action_normalize", False)),
                    "playback_mode": self.hdf5_replay_playback_mode,
                }
                return self.zero_action
        action = self.hdf5_replay_actions[self.robot_replay_step]
        self.last_robot_action_record = {
            "source": "hdf5_replay",
            "replay_id": self.hdf5_replay_id,
            "step": int(self.robot_replay_step),
            "total_steps": int(len(self.hdf5_replay_actions)),
            "vector": self.compact_vector(action),
            "controller": self.hdf5_controller_summary(),
            "normalized": bool((self.hdf5_replay_config or {}).get("robot_config", {}).get("action_normalize", False)),
            "playback_mode": self.hdf5_replay_playback_mode,
        }
        if self.robot_replay_step % 30 == 0:
            bridge.log(
                "hdf5_action_replay_step",
                {
                    "replay_id": self.hdf5_replay_id,
                    "step": int(self.robot_replay_step),
                    "total": int(len(self.hdf5_replay_actions)),
                    "playback_mode": self.hdf5_replay_playback_mode,
                },
            )
        self.robot_replay_step += 1
        return action

    def compact_vector(self, values, max_values=32):
        if values is None:
            return None
        try:
            raw = values.tolist() if hasattr(values, "tolist") else list(values)
        except Exception:
            return None
        return {
            "values": [float(value) for value in raw[:max_values]],
            "length": len(raw),
            "truncated": len(raw) > max_values,
        }

    def hdf5_controller_summary(self):
        robot_config = (self.hdf5_replay_config or {}).get("robot_config") or {}
        controller_config = robot_config.get("controller_config") or {}
        if not controller_config:
            return None
        return {
            "groups": sorted(str(name) for name in controller_config),
            "action_normalize": bool(robot_config.get("action_normalize", False)),
        }

    def update_heavy_load(self, sim_t):
        return

    def read_sensors(self):
        dummy_pos = _get_dummy_position(self.dummy_root).tolist()
        with og.sim.editing_usd():
            self.current_readings = self.sensor_rig.read(dummy_pos, [])
        motion_readings = self.current_readings["motion_sensors"]
        detected_motions = [
            (sensor_id, reading)
            for sensor_id, reading in motion_readings.items()
            if reading.get("detected")
        ]
        if detected_motions:
            active_sensor_id, motion = min(
                detected_motions,
                key=lambda item: float(item[1].get("distance") or float("inf")),
            )
        else:
            active_sensor_id, motion = next(iter(motion_readings.items()))
        pressure = self.current_readings["pressure_sensors"][self.pressure_sensor_name]
        detected = bool(motion["detected"])
        active_zone = motion.get("zone") if detected else None
        self.state.human.position = dummy_pos
        self.state.human.heading_deg = float(self.human_heading_deg)
        self.state.human.zone = active_zone if active_zone else "unknown"
        self.state.motion.update_zone(
            active_zone,
            detected=detected,
            distance_m=motion.get("distance"),
            sensor_id=active_sensor_id,
        )
        self.state.pressure.weight_kg = float(pressure.get("estimated_weight_kg", 0.0))
        virtual_weight_kg = self.activity_state.virtual_sensors.get("laundry_weight_kg")
        if virtual_weight_kg is not None:
            self.state.pressure.weight_kg = float(virtual_weight_kg)
        self.state.pressure.threshold_kg = 6.0
        self.resident_context = self.activity_simulator.estimate_context(
            self.activity_state,
            motion_zone=active_zone,
            motion_detected=detected,
            last_known_zone=self.state.motion.last_known_zone,
        )
        self.state.robot.context = {
            "resident_zone": self.state.human.zone,
            "last_known_resident_zone": self.state.motion.last_known_zone,
            "resident_position": dummy_pos,
            "active_motion_sensor": self.state.motion.active_sensor_id,
            "pressure_triggered": self.state.pressure.triggered,
            "laundry_weight_kg": self.state.pressure.weight_kg,
            "resident_context": self.resident_context,
            "activity_id": self.activity_state.activity_id,
            "virtual_sensors": dict(self.activity_state.virtual_sensors),
            "ground_truth_resident_zone": self.ground_truth_resident_zone(),
            "scenario_type": self.episode_scenario_type or self.infer_scenario_type(),
            "sensor_layout": self.sensor_layout,
        }

    def snapshot(self):
        data = self.state.to_dict()
        data["readings"] = self.current_readings
        data["source"] = "omnigibson-live-scene"
        data["human_collision"] = {
            "mode": self.args.human_collision_mode,
            "proxy_visible": bool(self.args.show_human_collision_proxy),
        }
        data["sensor_visualization"] = {
            "motion_ranges_visible": bool(self.sensor_ranges_visible),
        }
        data["sensor_layout"] = {
            "active": self.sensor_layout,
            "available": self.ordered_sensor_layout_names(),
            "active_motion_sensors": list(getattr(self.sensor_rig, "active_motion_sensor_names", []))
            if self.sensor_rig is not None
            else [],
        }
        data["activity_context"] = {
            "enabled": bool(self.activity_state.enabled),
            "activity_id": self.activity_state.activity_id,
            "ground_truth_zone": self.activity_state.ground_truth_zone,
            "posture": self.activity_state.posture,
            "movement_enabled": bool(self.activity_state.movement_enabled),
            "virtual_sensors": dict(self.activity_state.virtual_sensors),
            "resident_context": dict(self.resident_context),
        }
        data["scenario_type"] = self.episode_scenario_type or self.infer_scenario_type()
        data["ground_truth"] = self.ground_truth_snapshot()
        data["estimates"] = self.estimate_snapshot()
        data["sensor_quality"] = self.sensor_quality_snapshot()
        data["risk"] = self.risk_snapshot()
        data["objects"] = data["ground_truth"].get("objects", {})
        data["action"] = dict(self.last_robot_action_record)
        data["hdf5_replay"] = {
            "active": bool(self.robot_replay_active),
            "paused": bool(self.robot_replay_paused),
            "replay_id": self.hdf5_replay_id,
            "step": int(self.robot_replay_step),
            "playback_mode": self.hdf5_replay_playback_mode,
            "available": [path.stem for path in self.hdf5_replay_paths],
        }
        data["task_evaluation"] = self.task_evaluation_snapshot()
        data["training_validity"] = self.training_validity_snapshot()
        data["robot_pose"] = data["ground_truth"]["robot_pose"]
        data["episode_phase"] = self.episode_phase()
        return data

    def log_step(self, now, sim_t, frame):
        if self.step_log_interval_s is None or self.episode_id <= 0:
            return
        if now - self.last_step_log_t < self.step_log_interval_s:
            return
        self.last_step_log_t = now
        data = self.snapshot()
        camera_frames = self.capture_dataset_camera_frames(now, sim_t, frame)
        self.episode_logger.write_step(
            {
                "schema_version": "homesense_step_v1",
                "episode_id": self.episode_id,
                "episode_seed": self.episode_seed,
                "episode_zone": self.episode_zone,
                "scenario_type": data.get("scenario_type"),
                "episode_phase": data.get("episode_phase"),
                "frame": int(frame),
                "sim_time_s": float(sim_t),
                "wall_time_s": float(now),
                "dataset": {
                    "run_dir": None if self.episode_logger.run_dir is None else str(self.episode_logger.run_dir),
                    "camera_frames": camera_frames,
                },
                "resident": {
                    "state": data.get("human"),
                    "ground_truth": {
                        "zone": data.get("ground_truth", {}).get("resident_zone"),
                        "position": data.get("ground_truth", {}).get("resident_position"),
                        "heading_deg": data.get("ground_truth", {}).get("resident_heading_deg"),
                        "velocity": data.get("ground_truth", {}).get("resident_velocity"),
                        "activity_id": data.get("ground_truth", {}).get("activity_id"),
                        "posture": data.get("ground_truth", {}).get("posture"),
                    },
                },
                "sensors": {
                    "readings": data.get("readings"),
                    "motion": data.get("motion"),
                    "pressure": data.get("pressure"),
                    "layout": data.get("sensor_layout"),
                    "quality": data.get("sensor_quality"),
                    "virtual": data.get("activity_context", {}).get("virtual_sensors"),
                },
                "action": data.get("action"),
                "task_evaluation": data.get("task_evaluation"),
                "objects": data.get("objects"),
                "safety": data.get("risk"),
                "human": data.get("human"),
                "motion": data.get("motion"),
                "pressure": data.get("pressure"),
                "robot": data.get("robot"),
                "robot_pose": data.get("robot_pose"),
                "camera_mode": data.get("camera_mode"),
                "sensor_layout": data.get("sensor_layout"),
                "activity_context": data.get("activity_context"),
                "ground_truth": data.get("ground_truth"),
                "estimates": data.get("estimates"),
                "sensor_quality": data.get("sensor_quality"),
                "risk": data.get("risk"),
                "training_validity": data.get("training_validity"),
                "metrics": dict(self.episode_metrics),
            }
        )

    def flush_sensor_visual_history(self):
        if self.sensor_visual_flush_frames <= 0:
            return
        self.sensor_visual_flush_frames -= 1
        try:
            og.sim.render()
        except Exception as exc:
            self.sensor_visual_flush_frames = 0
            bridge.log("sensor_visual_flush_failed", {"reason": str(exc)})

    def run(self):
        frame = 0
        while True:
            sim_t = frame * og.sim.get_sim_step_dt()
            self.process_commands()
            now = time()
            self.sync_viewport_input(now)
            self.update_human_motion()
            self.update_heavy_load(sim_t)
            self.read_sensors()
            self.update_episode_metrics()
            self.update_task()
            if frame > 2 and self.ceiling_visibility_dirty:
                visibility_result = self.sync_ceiling_visibility_for_view()
                if visibility_result["event"] == "ceiling_visibility":
                    bridge.log(visibility_result["event"], visibility_result)
            self.update_follow_camera(now)
            self.log_step(now, sim_t, frame)
            self.update_viewport_hud(now)
            self.flush_sensor_visual_history()
            self.capture_video_frame(now)
            frame += 1
            self.env.step(action=self.next_robot_action())


def start_server(host, port):
    thread = threading.Thread(
        target=lambda: uvicorn.run("smart_home.live.server:app", host=host, port=port, log_level="info"),
        daemon=True,
    )
    thread.start()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preset", choices=sorted(PRESETS), default="wider_house")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--serve-client",
        action="store_true",
        help="Also start the legacy FastAPI browser/Electron gateway. Viewport-only demos leave this disabled.",
    )
    parser.add_argument("--scene-model", default=None, help="Override the preset BEHAVIOR scene model, e.g. Merom_0_int.")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--clean-structure-materials", action="store_true", default=True)
    parser.add_argument("--task-duration-s", type=float, default=6.0)
    parser.add_argument("--video-fps", type=float, default=10.0)
    parser.add_argument("--episode-seed", type=int, default=20260530, help="Seed for reproducible episode resident randomization.")
    parser.add_argument(
        "--resident-zone",
        default="random",
        help="Resident spawn zone for new episodes. Use 'random' to sample from the current scene profile zones.",
    )
    parser.add_argument(
        "--episode-log-dir",
        default="datasets/homesense_episodes",
        help="Directory, relative to the project root unless absolute, for HomeSense training dataset runs.",
    )
    parser.add_argument("--disable-episode-logging", action="store_true", help="Disable JSONL episode logging.")
    parser.add_argument(
        "--enable-activity-sensors",
        action="store_true",
        help="Enable scene-profile virtual activity sensors for data-generation episodes.",
    )
    parser.add_argument(
        "--sensor-layout",
        choices=["current", "dense", "sparse"],
        default="current",
        help="Scene-profile motion sensor layout variant for data diversity.",
    )
    parser.add_argument(
        "--step-log-hz",
        type=float,
        default=2.0,
        help="Write compact step-level episode data at this frequency. Set 0 to disable.",
    )
    parser.add_argument(
        "--save-camera-frames",
        action="store_true",
        help="Save sampled camera frames into the episode run directory. Disabled by default to avoid heavy disk writes.",
    )
    parser.add_argument(
        "--camera-log-fps",
        type=float,
        default=2.0,
        help="Camera frame sampling frequency when --save-camera-frames is enabled.",
    )
    parser.add_argument(
        "--camera-log-sources",
        default="robot",
        help="Comma-separated camera frame sources to save: robot, top, or all. Top uses the viewer camera only in overview mode.",
    )
    parser.add_argument(
        "--camera-log-width",
        type=int,
        default=640,
        help="Resize saved camera JPEGs to this maximum width. Use 0 to keep the captured width.",
    )
    parser.add_argument(
        "--camera-log-quality",
        type=int,
        default=80,
        help="JPEG quality for saved camera frames.",
    )
    parser.add_argument(
        "--disable-viewport-hud",
        action="store_true",
        help="Hide the Omni UI context HUD in the simulator viewport.",
    )
    parser.add_argument(
        "--randomize-resident-on-reset",
        action="store_true",
        help="Make R/reset start a randomized episode instead of returning to the configured resident start pose.",
    )
    parser.add_argument("--robot-type", choices=["R1", "R1Pro"], default="R1Pro")
    parser.add_argument(
        "--hdf5-replay",
        default=None,
        help="Optional HDF5 action replay to run inside the HomeSense scene when T / deliver_item is triggered.",
    )
    parser.add_argument(
        "--hdf5-replay-dir",
        action="append",
        default=["../replay-data"],
        help="Directory containing selectable HDF5 replays. Can be passed multiple times.",
    )
    parser.add_argument("--hdf5-replay-episode", type=int, default=0)
    parser.add_argument(
        "--hdf5-replay-playback",
        choices=["auto", "action", "state"],
        default="auto",
        help="Replay actions only, or restore recorded simulator state before each action. Auto uses state playback when no base controller is recorded.",
    )
    parser.add_argument(
        "--hdf5-replay-scene-source",
        choices=["auto", "profile", "hdf5"],
        default="auto",
        help="Use the scene profile JSON or the scene JSON embedded in the HDF5. Auto uses the embedded scene for state playback.",
    )
    parser.add_argument("--cpu-dynamics", action="store_true", help="Use CPU dynamics instead of GPU dynamics for heavier full-scene demos.")
    parser.add_argument(
        "--flatcache",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override OmniGibson flatcache. Doorless Merom disables it by default for stability.",
    )
    parser.add_argument("--empty-scene", action="store_true", help="Load an empty Isaac/OmniGibson stage instead of BEHAVIOR scene assets.")
    parser.add_argument("--preserve-ceiling", action="store_true", help="Do not hide ceiling/roof prims for top-down demo views.")
    parser.add_argument(
        "--open-doors",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Deprecated unsafe runtime shortcut. Doorless demos should use the generated doorless scene JSON instead.",
    )
    parser.add_argument(
        "--doorless-scene",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="For Merom_0_int, load the generated scene JSON with interior door objects removed.",
    )
    parser.add_argument(
        "--human-collision-mode",
        choices=["solid", "ghost"],
        default="solid",
        help="Use solid for demo collision, or ghost when validating replay paths without resident obstruction.",
    )
    parser.add_argument(
        "--show-human-collision-proxy",
        action="store_true",
        help="Render the simplified resident collision capsule for debugging.",
    )
    parser.add_argument(
        "--human-visual-mode",
        choices=["procedural", "usd"],
        default="procedural",
        help="Use the stable standing procedural avatar by default; usd is available for asset debugging but may show T-pose.",
    )
    args = parser.parse_args()
    preset = PRESETS[args.preset]
    args.pressure_pos = preset["pressure_pos"]

    scene = LiveControlledScene(args)
    scene.setup()
    if args.serve_client:
        start_server(args.host, args.port)
        print(f"Live control server: http://{args.host}:{args.port}", flush=True)
    else:
        print("FastAPI/Electron gateway disabled. Use the OmniGibson / Isaac Sim viewport controls.", flush=True)
    scene.run()


if __name__ == "__main__":
    main()
