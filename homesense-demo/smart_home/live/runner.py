from __future__ import annotations

import argparse
import math
import queue
import random
import sys
import threading
from pathlib import Path
from time import time

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch as th
import uvicorn

import omnigibson as og
import omnigibson.lazy as lazy
import omnigibson.utils.transform_utils as T
from omnigibson.macros import gm
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
from smart_home.live.media import rgb_obs_to_jpeg, zero_action_like
from smart_home.replay import ReplayRegistry, ReplaySelectionError
from smart_home.scene_profile import load_scene_profile
from smart_home.sensors import SmartHomeSensorRig
from smart_home.service_types import SmartHomeState, TaskCommand


def doorless_scene_file_for(repo_root, scene_profile):
    if scene_profile is None or not scene_profile.doorless_scene_file:
        return None
    path = Path(scene_profile.doorless_scene_file)
    return path if path.is_absolute() else repo_root / path


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
        self.follow_camera_interval_s = 0.10
        self.last_follow_camera_t = 0.0
        self.robot_rgb_sensor = None
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
        self.viewport_camera_modes = ("overview", "resident", "robot")
        self.viewport_input_active = False
        self.viewport_input_expires_t = 0.0
        self.viewport_input_vector = (0.0, 0.0, 0.0)
        self.viewport_input_face_movement = True
        self.scene_profile = None
        self.pressure_sensor_name = "pressure_sensor_0"
        self.rng = random.Random(args.episode_seed)
        self.episode_logger = EpisodeJsonlLogger(REPO_ROOT / args.episode_log_dir, enabled=not args.disable_episode_logging)
        self.episode_id = 0
        self.episode_started_at = None
        self.episode_seed = args.episode_seed
        self.episode_zone = None
        self.episode_metrics = self.empty_episode_metrics()
        self.activity_simulator = ActivitySensorSimulator(enabled=False)
        self.activity_state = ActivityState()
        self.resident_context = {}
        self.step_log_interval_s = 1.0 / max(float(args.step_log_hz), 0.001) if args.step_log_hz > 0 else None
        self.last_step_log_t = 0.0
        self.hud_window = None
        self.hud_labels = {}
        self.last_hud_update_t = 0.0

    def setup(self):
        preset = PRESETS[self.args.preset]
        scene_model = self.args.scene_model or preset["scene"]
        self.scene_profile = load_scene_profile(REPO_ROOT, scene_model)
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
        using_doorless_scene = bool(self.args.doorless_scene and self.scene_profile and self.scene_profile.doorless_scene_file)
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
            scene_file = doorless_scene_file_for(REPO_ROOT, self.scene_profile) if self.args.doorless_scene else None
            scene_cfg = {
                "type": "InteractiveTraversableScene",
                "scene_model": scene_model,
                "load_object_categories": list(STRUCTURE_CATEGORIES) if not self.args.full else None,
                "include_robots": True,
            }
            if scene_file is not None:
                if not scene_file.exists():
                    raise FileNotFoundError(
                        f"Doorless scene file is missing: {scene_file}. "
                        "Generate it before launching, or pass --no-doorless-scene."
                    )
                scene_cfg["scene_file"] = str(scene_file)
                bridge.log("doorless_scene_file_selected", {"scene_model": scene_model, "scene_file": str(scene_file)})
        cfg = {
            "scene": scene_cfg,
            "robots": [
                {
                    "type": self.args.robot_type,
                    "obs_modalities": ["rgb"],
                    "action_type": "continuous",
                    "action_normalize": True,
                    "scale": 1.0,
                    "self_collision": False,
                }
            ],
            "task": {"type": "DummyTask"},
        }
        if self.args.full and not self.args.empty_scene:
            cfg["scene"].pop("load_object_categories", None)

        self.env = og.Environment(configs=cfg)
        self.robot = self.env.robots[0] if self.env.robots else None
        self.apply_robot_initial_pose()
        self.robot_rgb_sensor = self._find_robot_rgb_sensor()
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
        return [dict(spec) for spec in self.scene_profile.motion_sensors]

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
            "task_started": False,
            "task_completed": False,
            "task_blocked": False,
            "last_task": None,
            "last_replay_id": None,
        }

    def write_dataset_manifest(self, scene_model):
        scene_file = doorless_scene_file_for(REPO_ROOT, self.scene_profile) if self.args.doorless_scene else None
        self.episode_logger.write_manifest(
            {
                "schema_version": "homesense_episode_dataset_v1",
                "scene_model": scene_model,
                "scene_file": str(scene_file) if scene_file is not None else None,
                "robot_type": self.args.robot_type,
                "resident_zone_mode": self.args.resident_zone,
                "episode_seed": self.episode_seed,
                "activity_sensors_enabled": bool(self.args.enable_activity_sensors),
                "step_log_hz": float(self.args.step_log_hz),
                "files": {
                    "events": "events.jsonl",
                    "steps": "steps.jsonl",
                    "manifest": "manifest.json",
                },
            }
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
        self.command_queue.put(("move_human_delta", (float(dx), float(dy), float(dz), bool(face_movement))))
        return {"event": "move_human_queued", "dx": dx, "dy": dy, "dz": dz, "face_movement": bool(face_movement)}

    def queue_set_human_input(self, dx, dy, dz=0.0, face_movement=True):
        if self.state.robot.busy or self.pending_robot_task:
            return {"event": "set_human_input_blocked", "reason": "robot task is running"}
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

    def queue_reset_scene(self):
        self.command_queue.put(("reset_scene", ()))
        return {"event": "reset_queued"}

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
            elif command == "reset_scene":
                result = self.reset_scene()
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
                result = {"event": "set_sensor_ranges_visible", "visible": self.sensor_ranges_visible}
            else:
                result = {"event": "unknown_command", "command": command}
            bridge.log(result.get("event", command), result)


    def _find_robot_rgb_sensor(self):
        if self.robot is None:
            return None
        for sensor in self.robot.sensors.values():
            if "rgb" in sensor.modalities:
                return sensor
        return None

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
        key = getattr(lazy.carb.input.KeyboardInput, "R", None)
        if key is not None:
            KeyboardEventHandler.add_keyboard_callback(key, self.queue_reset_scene)
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
            "  3: Robot follow camera\n"
            "  C: Cycle camera mode\n"
            "  W/A/S/D or arrow keys: Move resident in overview; move relative to resident in resident follow\n"
            "  Q/E: Rotate resident in resident follow\n"
            "  F: Toggle motion sensor ranges\n"
            "  N: Start a randomized data-collection episode\n"
            "  T/Y: Trigger deliver-item / laundry replay placeholder\n"
            "  R: Reset scene\n"
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
        self.hud_window = ui.Window("HomeSense Live Context", width=390, height=230, visible=True)
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
        values = {
            "episode": f"Episode: {self.episode_id} / zone={self.episode_zone}",
            "zone": f"Resident: {self.state.human.zone} pos={self._short_vec(self.state.human.position)}",
            "motion": f"Motion: {self.state.motion.active_sensor_id or '--'} detected={self.state.motion.detected}",
            "activity": f"Activity: {self.activity_state.activity_id or '--'} confidence={confidence_text}",
            "virtual": f"Virtual sensors: {virtual_summary}",
            "task": f"Robot task: {self.state.robot.task or '--'} status={self.state.robot.status}",
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
        if self.state.robot.busy or now > self.viewport_input_expires_t:
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
        if self._movement_blocker(preferred) is None:
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

    def sample_resident_position_for_zone(self, zone_name):
        if self.scene_profile is None or zone_name not in self.scene_profile.zones:
            return self.human_start_position(PRESETS[self.args.preset])
        zone = self.scene_profile.zones[zone_name]
        center = zone.get("center")
        if not center:
            return self.human_start_position(PRESETS[self.args.preset])
        spawn_points = zone.get("spawn_points") or []
        if spawn_points:
            shuffled = [list(point) for point in spawn_points]
            self.rng.shuffle(shuffled)
            for point in shuffled:
                base = [float(point[0]), float(point[1]), float(point[2]) if len(point) > 2 else 0.0]
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
                    if self._movement_blocker(candidate) is None:
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
            if self._movement_blocker(candidate) is None:
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

    def update_episode_metrics(self):
        self.episode_metrics["frame_count"] += 1
        distance = self.current_robot_resident_distance()
        if distance is None:
            return
        previous = self.episode_metrics["min_robot_resident_distance_m"]
        if previous is None or distance < float(previous):
            self.episode_metrics["min_robot_resident_distance_m"] = distance

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
            "resident": data.get("human"),
            "motion": data.get("motion"),
            "pressure": data.get("pressure"),
            "activity_context": data.get("activity_context"),
            "robot_context": data.get("robot", {}).get("context"),
            "metrics": dict(self.episode_metrics),
        }

    def finish_episode(self, reason):
        if self.episode_id <= 0:
            return
        self.episode_logger.write("episode_end", self.episode_payload(event_reason=reason))

    def start_episode(self, zone="random", reason="manual", randomize=True):
        if self.state.robot.busy:
            return {"event": "new_episode_blocked", "reason": "robot task is running"}
        if self.episode_id > 0:
            self.finish_episode(reason=f"superseded_by_{reason}")
        self.clear_viewport_input()
        self.human_input_vector = (0.0, 0.0, 0.0)
        self.state.robot.status = "idle"
        self.state.robot.task = None
        self.state.robot.replay_id = None
        self.robot_task_end_t = None
        self.episode_id += 1
        self.episode_started_at = utc_now_iso()
        self.episode_metrics = self.empty_episode_metrics()
        if randomize:
            self.episode_zone = self.choose_episode_zone(zone)
            resident_pos = self.sample_resident_position_for_zone(self.episode_zone)
        else:
            self.episode_zone = "configured_start"
            resident_pos = self.human_start_position(PRESETS[self.args.preset])
        self.human_target_pos = self.find_nearest_free_position(resident_pos)
        self.human_heading_deg = 0.0
        self._set_human_pose(self.human_target_pos, self.human_heading_deg)
        self.activity_state = self.activity_simulator.start_episode(self.episode_zone, self.rng)
        self.set_camera("overview")
        self.read_sensors()
        payload = self.episode_payload(event_reason=reason)
        payload["sampled_position"] = self.human_target_pos
        self.episode_logger.write("episode_start", payload)
        bridge.log(
            "episode_started",
            {
                "episode_id": self.episode_id,
                "zone": self.episode_zone,
                "position": self.human_target_pos,
                "log_path": str(self.episode_logger.path) if self.episode_logger.path else None,
            },
        )
        return {"event": "episode_started", "episode_id": self.episode_id, "zone": self.episode_zone, "position": self.human_target_pos}

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
                position = [robot_pos[0] - forward[0] * 1.85, robot_pos[1] - forward[1] * 1.85, robot_pos[2] + 1.45]
                look_at = [robot_pos[0] + forward[0] * 0.45, robot_pos[1] + forward[1] * 0.45, robot_pos[2] + 0.75]
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

    def set_camera(self, mode):
        mode, position, look_at = self._viewer_camera_pose(mode)
        self._apply_viewer_camera_pose(mode, position, look_at)
        self.state.camera_mode = mode
        self.mark_ceiling_visibility_dirty()
        return {"mode": mode, "position": position, "look_at": look_at}

    def update_follow_camera(self, now):
        if self.video_source != "viewer" or self.state.camera_mode not in {"robot", "resident"}:
            return
        if now - self.last_follow_camera_t < self.follow_camera_interval_s:
            return
        self.last_follow_camera_t = now
        mode, position, look_at = self._viewer_camera_pose(self.state.camera_mode)
        self._apply_viewer_camera_pose(mode, position, look_at)

    def run_task(self, task):
        if self.state.robot.busy:
            return {"event": "task_blocked", "task": task, "reason": "robot already busy"}
        self.clear_viewport_input()
        self.human_input_vector = (0.0, 0.0, 0.0)
        self.human_target_pos = _get_dummy_position(self.dummy_root).tolist()
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
        self.episode_metrics["task_started"] = True
        self.episode_metrics["last_task"] = task
        self.episode_metrics["last_replay_id"] = replay.replay_id
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
        self.human_target_pos = self.find_nearest_free_position(human_start_pos)
        self.human_heading_deg = 0.0
        self._set_human_pose(self.human_target_pos, self.human_heading_deg)
        self.state.robot.status = "idle"
        self.state.robot.task = None
        self.state.robot.replay_id = None
        self.robot_task_end_t = None
        self.set_camera("overview")
        self.read_sensors()
        return {"position": self.human_target_pos}

    def update_task(self):
        if self.robot_task_end_t is not None and time() >= self.robot_task_end_t:
            self.state.robot.status = "completed"
            self.robot_task_end_t = None
            self.episode_metrics["task_completed"] = True
            self.episode_logger.write("task_completed", self.episode_payload(event_reason="task_duration_elapsed"))

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
        data["activity_context"] = {
            "enabled": bool(self.activity_state.enabled),
            "activity_id": self.activity_state.activity_id,
            "ground_truth_zone": self.activity_state.ground_truth_zone,
            "virtual_sensors": dict(self.activity_state.virtual_sensors),
            "resident_context": dict(self.resident_context),
        }
        data["robot_pose"] = self.current_robot_pose()
        return data

    def log_step(self, now, sim_t, frame):
        if self.step_log_interval_s is None or self.episode_id <= 0:
            return
        if now - self.last_step_log_t < self.step_log_interval_s:
            return
        self.last_step_log_t = now
        data = self.snapshot()
        self.episode_logger.write_step(
            {
                "episode_id": self.episode_id,
                "episode_seed": self.episode_seed,
                "episode_zone": self.episode_zone,
                "frame": int(frame),
                "sim_time_s": float(sim_t),
                "wall_time_s": float(now),
                "human": data.get("human"),
                "motion": data.get("motion"),
                "pressure": data.get("pressure"),
                "robot": data.get("robot"),
                "camera_mode": data.get("camera_mode"),
                "activity_context": data.get("activity_context"),
                "metrics": dict(self.episode_metrics),
            }
        )

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
            self.log_step(now, sim_t, frame)
            if frame > 2 and self.ceiling_visibility_dirty:
                visibility_result = self.sync_ceiling_visibility_for_view()
                if visibility_result["event"] == "ceiling_visibility":
                    bridge.log(visibility_result["event"], visibility_result)
            self.update_follow_camera(now)
            self.update_viewport_hud(now)
            self.capture_video_frame(now)
            frame += 1
            self.env.step(action=self.zero_action)


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
        default="logs/homesense_episodes",
        help="Directory, relative to the BEHAVIOR-1K root unless absolute, for JSONL episode logs.",
    )
    parser.add_argument("--disable-episode-logging", action="store_true", help="Disable JSONL episode logging.")
    parser.add_argument(
        "--enable-activity-sensors",
        action="store_true",
        help="Enable scene-profile virtual activity sensors for data-generation episodes.",
    )
    parser.add_argument(
        "--step-log-hz",
        type=float,
        default=2.0,
        help="Write compact step-level episode data at this frequency. Set 0 to disable.",
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
