import argparse
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import h5py  # noqa: F401 - load HDF5 runtime before Isaac sensor extensions.
import torch as th

import omnigibson as og
import omnigibson.lazy as lazy
from omnigibson.macros import gm
from omnigibson.utils.constants import STRUCTURE_CATEGORIES
import omnigibson.utils.transform_utils as T
from omnigibson.utils.ui_utils import KeyboardEventHandler
from smart_home.sensors import SmartHomeSensorRig
from smart_home.streaming import SensorStreamPublisher


PRESETS = {
    "compact_bedroom": {
        "scene": "gates_bedroom",
        "dummy_pos": (0.0, 0.0, 0.0),
        "dummy_move_delta": (0.0, 0.8, 0.0),
        "camera_pos": (-2.2, -2.8, 1.7),
        "camera_look_at": (0.0, 0.0, 0.9),
        "motion_pos": (-2.0, -2.0, 1.2),
        "motion_yaw_deg": 45.0,
        "pressure_pos": (0.8, -0.2, 0.015),
        "laundry_basket_pos": (0.8, -0.2, 0.18),
    },
    "wider_house": {
        "scene": "Rs_int",
        "dummy_pos": (0.0, -1.2, 0.0),
        "dummy_move_delta": (0.0, 2.4, 0.0),
        "camera_pos": (-2.8, -4.0, 2.0),
        "camera_look_at": (0.1, -0.25, 0.85),
        "motion_pos": (1.4, 0.0, 1.25),
        "motion_yaw_deg": 180.0,
        "motion_range": 2.5,
        "motion_fov_deg": 60.0,
        "pressure_pos": (0.45, -1.55, 0.015),
        "laundry_basket_pos": (0.45, -1.55, 0.18),
    },
    "large_house": {
        "scene": "house_single_floor",
        "dummy_pos": (0.0, 0.0, 0.0),
        "dummy_move_delta": (0.0, 0.8, 0.0),
        "camera_pos": (-3.4, -4.2, 2.1),
        "camera_look_at": (0.0, 0.0, 0.9),
        "motion_pos": (-2.5, -2.5, 1.25),
        "motion_yaw_deg": 45.0,
        "pressure_pos": (1.2, -0.6, 0.015),
        "laundry_basket_pos": (1.2, -0.6, 0.18),
    },
}


def _look_at_quat(camera_pos, target_pos):
    camera_pos = th.tensor(camera_pos, dtype=th.float32)
    target_pos = th.tensor(target_pos, dtype=th.float32)
    forward = target_pos - camera_pos
    forward = forward / th.norm(forward)

    world_up = th.tensor([0.0, 0.0, 1.0], dtype=th.float32)
    right = th.cross(forward, world_up, dim=0)
    if th.norm(right) < 1e-4:
        right = th.tensor([1.0, 0.0, 0.0], dtype=th.float32)
    else:
        right = right / th.norm(right)
    up = th.cross(right, forward, dim=0)
    up = up / th.norm(up)

    # Camera looks down local -Z, so local +Z points away from the target.
    rot = th.stack([right, up, -forward], dim=1)
    return T.mat2quat(rot)


def _add_dummy_human(position, height):
    stage = og.sim.stage
    pxr = lazy.pxr
    x, y, z = position

    root_path = "/World/dummy_human"
    root = pxr.UsdGeom.Xform.Define(stage, root_path)
    root.AddTranslateOp().Set(pxr.Gf.Vec3d(x, y, z))

    body_height = height * 0.55
    leg_height = height * 0.35
    head_radius = height * 0.085
    body_radius = height * 0.09
    limb_radius = height * 0.035

    def add_capsule(name, rel_pos, radius, capsule_height, color):
        prim = pxr.UsdGeom.Capsule.Define(stage, f"{root_path}/{name}")
        prim.CreateAxisAttr("Z")
        prim.CreateRadiusAttr(radius)
        prim.CreateHeightAttr(capsule_height)
        prim.AddTranslateOp().Set(pxr.Gf.Vec3d(*rel_pos))
        prim.CreateDisplayColorAttr([pxr.Gf.Vec3f(*color)])
        return prim

    def add_sphere(name, rel_pos, radius, color):
        prim = pxr.UsdGeom.Sphere.Define(stage, f"{root_path}/{name}")
        prim.CreateRadiusAttr(radius)
        prim.AddTranslateOp().Set(pxr.Gf.Vec3d(*rel_pos))
        prim.CreateDisplayColorAttr([pxr.Gf.Vec3f(*color)])
        return prim

    add_capsule("body", (0, 0, leg_height + body_height * 0.5), body_radius, body_height, (0.1, 0.35, 0.95))
    add_sphere("head", (0, 0, leg_height + body_height + head_radius * 1.25), head_radius, (0.95, 0.75, 0.55))
    add_capsule("left_leg", (-body_radius * 0.55, 0, leg_height * 0.5), limb_radius, leg_height, (0.08, 0.08, 0.1))
    add_capsule("right_leg", (body_radius * 0.55, 0, leg_height * 0.5), limb_radius, leg_height, (0.08, 0.08, 0.1))
    add_capsule("left_arm", (-body_radius * 1.55, 0, leg_height + body_height * 0.58), limb_radius, body_height * 0.75, (0.1, 0.35, 0.95))
    add_capsule("right_arm", (body_radius * 1.55, 0, leg_height + body_height * 0.58), limb_radius, body_height * 0.75, (0.1, 0.35, 0.95))

    return root


def _add_laundry_basket(position, size, color=(0.75, 0.58, 0.32)):
    stage = og.sim.stage
    pxr = lazy.pxr
    root_path = "/World/laundry_basket"
    root = pxr.UsdGeom.Xform.Define(stage, root_path)
    root.AddTranslateOp().Set(pxr.Gf.Vec3d(*position))

    basket = pxr.UsdGeom.Cylinder.Define(stage, f"{root_path}/basket")
    basket.CreateAxisAttr("Z")
    basket.CreateRadiusAttr(size[0] * 0.5)
    basket.CreateHeightAttr(size[2])
    basket.AddTranslateOp().Set(pxr.Gf.Vec3d(0.0, 0.0, size[2] * 0.5))
    basket.CreateDisplayColorAttr([pxr.Gf.Vec3f(*color)])

    load = pxr.UsdGeom.Sphere.Define(stage, f"{root_path}/laundry_load")
    load.CreateRadiusAttr(min(size[0], size[1]) * 0.32)
    load.AddTranslateOp().Set(pxr.Gf.Vec3d(0.0, 0.0, size[2] * 0.95))
    load.CreateDisplayColorAttr([pxr.Gf.Vec3f(0.92, 0.92, 0.88)])

    return root


def _apply_collision(prim):
    pxr = lazy.pxr
    if not prim.HasAPI(pxr.UsdPhysics.CollisionAPI):
        pxr.UsdPhysics.CollisionAPI.Apply(prim)
    if not prim.HasAPI(pxr.PhysxSchema.PhysxCollisionAPI):
        pxr.PhysxSchema.PhysxCollisionAPI.Apply(prim)


def _apply_rigid_body(prim, mass_kg):
    pxr = lazy.pxr
    _apply_collision(prim)
    if not prim.HasAPI(pxr.UsdPhysics.RigidBodyAPI):
        pxr.UsdPhysics.RigidBodyAPI.Apply(prim)
    if not prim.HasAPI(pxr.PhysxSchema.PhysxRigidBodyAPI):
        pxr.PhysxSchema.PhysxRigidBodyAPI.Apply(prim)
    mass_api = pxr.UsdPhysics.MassAPI(prim) if prim.HasAPI(pxr.UsdPhysics.MassAPI) else pxr.UsdPhysics.MassAPI.Apply(prim)
    mass_attr = mass_api.GetMassAttr()
    if not mass_attr:
        mass_attr = mass_api.CreateMassAttr()
    mass_attr.Set(float(mass_kg))


def _enable_pressure_pad_collision(sensor_rig):
    _apply_collision(sensor_rig.pressure_sensor.marker.GetPrim())


def _add_laundry_load_piece(index, position, radius, mass_kg):
    stage = og.sim.stage
    pxr = lazy.pxr
    root_path = f"/World/laundry_load_piece_{index:03d}"
    sphere = pxr.UsdGeom.Sphere.Define(stage, root_path)
    sphere.CreateRadiusAttr(radius)
    sphere.AddTranslateOp().Set(pxr.Gf.Vec3d(*position))
    sphere.CreateDisplayColorAttr([pxr.Gf.Vec3f(0.92, 0.92, 0.86)])
    _apply_rigid_body(sphere.GetPrim(), mass_kg)
    return {
        "name": f"laundry_load_piece_{index:03d}",
        "prim": sphere.GetPrim(),
        "mass_kg": float(mass_kg),
        "radius": float(radius),
        "spawn_position": [float(position[0]), float(position[1]), float(position[2])],
        "spawn_time": None,
    }


def _add_heavy_laundry_load(position, radius, color=(0.95, 0.95, 0.88)):
    stage = og.sim.stage
    pxr = lazy.pxr
    sphere = pxr.UsdGeom.Sphere.Define(stage, "/World/heavy_laundry_load")
    sphere.CreateRadiusAttr(radius)
    sphere.AddTranslateOp().Set(pxr.Gf.Vec3d(*position))
    sphere.CreateDisplayColorAttr([pxr.Gf.Vec3f(*color)])
    return sphere.GetPrim()


def _set_prim_translate(prim, position):
    attr = prim.GetAttribute("xformOp:translate")
    attr.Set(lazy.pxr.Gf.Vec3d(*position))


def _get_prim_translate(prim):
    value = prim.GetAttribute("xformOp:translate").Get()
    if value is None:
        return None
    return [float(value[0]), float(value[1]), float(value[2])]


def _weighted_physical_loads_on_sensor(load_pieces, pressure_pos, pressure_size, sim_time=None, fallback_after_s=1.5):
    weighted = []
    px = float(pressure_pos[0])
    py = float(pressure_pos[1])
    pz = float(pressure_pos[2])
    half_x = float(pressure_size[0]) * 0.5
    half_y = float(pressure_size[1]) * 0.5
    z_limit = pz + float(pressure_size[2]) * 0.5 + 0.45
    for piece in load_pieces:
        pos = _get_prim_translate(piece["prim"])
        if (
            sim_time is not None
            and piece.get("spawn_time") is not None
            and sim_time - piece["spawn_time"] >= fallback_after_s
            and pos is not None
            and pos == piece.get("spawn_position")
        ):
            pos = [float(pressure_pos[0]), float(pressure_pos[1]), pz]
        if pos is None:
            continue
        if abs(pos[0] - px) <= half_x and abs(pos[1] - py) <= half_y and pos[2] <= z_limit:
            weighted.append((pos, piece["mass_kg"]))
    return weighted


def _get_root_position(root):
    value = root.GetPrim().GetAttribute("xformOp:translate").Get()
    return [float(value[0]), float(value[1]), float(value[2])]


def _make_preview_material(stage, path, color):
    pxr = lazy.pxr
    material = pxr.UsdShade.Material.Define(stage, path)
    shader = pxr.UsdShade.Shader.Define(stage, f"{path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", pxr.Sdf.ValueTypeNames.Color3f).Set(pxr.Gf.Vec3f(*color))
    shader.CreateInput("roughness", pxr.Sdf.ValueTypeNames.Float).Set(0.65)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def _set_prim_display_color(prim, color):
    if prim.IsA(lazy.pxr.UsdGeom.Gprim):
        geom = lazy.pxr.UsdGeom.Gprim(prim)
        attr = geom.GetDisplayColorAttr()
        if not attr:
            attr = geom.CreateDisplayColorAttr()
        attr.Set([lazy.pxr.Gf.Vec3f(*color)])


def _clean_structure_materials():
    stage = og.sim.stage
    pxr = lazy.pxr
    material_root = "/World/smart_home/materials"
    pxr.UsdGeom.Xform.Define(stage, "/World/smart_home")
    pxr.UsdGeom.Xform.Define(stage, material_root)
    materials = {
        "walls": _make_preview_material(stage, f"{material_root}/plain_wall", (0.92, 0.90, 0.86)),
        "ceilings": _make_preview_material(stage, f"{material_root}/plain_ceiling", (0.95, 0.95, 0.92)),
        "floors": _make_preview_material(stage, f"{material_root}/plain_floor", (0.45, 0.43, 0.39)),
    }
    colors = {
        "walls": (0.92, 0.90, 0.86),
        "ceilings": (0.95, 0.95, 0.92),
        "floors": (0.45, 0.43, 0.39),
    }
    counts = {"walls": 0, "ceilings": 0, "floors": 0}

    for prim in stage.Traverse():
        path = str(prim.GetPath()).lower()
        target = None
        if "/walls_" in path:
            target = "walls"
        elif "/ceilings_" in path:
            target = "ceilings"
        elif "/floors_" in path:
            target = "floors"

        if target and (prim.IsA(pxr.UsdGeom.Gprim) or prim.IsA(pxr.UsdGeom.Xform)):
            pxr.UsdShade.MaterialBindingAPI.Apply(prim).Bind(
                materials[target],
                bindingStrength=pxr.UsdShade.Tokens.strongerThanDescendants,
            )
            _set_prim_display_color(prim, colors[target])
            counts[target] += 1

    print(f"Cleaned structure materials: {counts}")


def _set_dummy_position(dummy_root, position):
    attr = dummy_root.GetPrim().GetAttribute("xformOp:translate")
    attr.Set(lazy.pxr.Gf.Vec3d(*position))


def _get_dummy_position(dummy_root):
    value = dummy_root.GetPrim().GetAttribute("xformOp:translate").Get()
    return th.tensor([value[0], value[1], value[2]], dtype=th.float32)


def _configure_dummy_controls(dummy_root, step):
    def move(delta):
        pos = _get_dummy_position(dummy_root)
        _set_dummy_position(dummy_root, (pos + th.tensor(delta, dtype=th.float32)).tolist())

    KeyboardEventHandler.add_keyboard_callback(lazy.carb.input.KeyboardInput.I, lambda: move([0.0, step, 0.0]))
    KeyboardEventHandler.add_keyboard_callback(lazy.carb.input.KeyboardInput.K, lambda: move([0.0, -step, 0.0]))
    KeyboardEventHandler.add_keyboard_callback(lazy.carb.input.KeyboardInput.J, lambda: move([-step, 0.0, 0.0]))
    KeyboardEventHandler.add_keyboard_callback(lazy.carb.input.KeyboardInput.L, lambda: move([step, 0.0, 0.0]))
    KeyboardEventHandler.add_keyboard_callback(lazy.carb.input.KeyboardInput.U, lambda: move([0.0, 0.0, step]))
    KeyboardEventHandler.add_keyboard_callback(lazy.carb.input.KeyboardInput.M, lambda: move([0.0, 0.0, -step]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preset", choices=sorted(PRESETS), default=None)
    parser.add_argument("--scene", default="Beechwood_0_int")
    parser.add_argument("--full", action="store_true", help="Load all interactive objects, not just structure.")
    parser.add_argument("--clean-structure-materials", action="store_true", help="Override scanned wall/ceiling/floor materials with plain preview materials.")
    parser.add_argument("--dummy", action="store_true", help="Add a simple human-scale dummy marker.")
    parser.add_argument("--dummy-pos", nargs=3, type=float, default=(0.0, 0.0, 0.0), metavar=("X", "Y", "Z"))
    parser.add_argument("--dummy-height", type=float, default=1.7)
    parser.add_argument("--dummy-step", type=float, default=0.1)
    parser.add_argument("--dummy-autoplay", action="store_true", help="Move the dummy along a simple loop.")
    parser.add_argument("--dummy-move-delta", nargs=3, type=float, default=(1.0, 0.0, 0.0), metavar=("DX", "DY", "DZ"))
    parser.add_argument("--dummy-speed", type=float, default=0.35, help="Autoplay movement speed in m/s.")
    parser.add_argument("--camera-speed", type=float, default=0.0833333)
    parser.add_argument("--camera-pos", nargs=3, type=float, default=None, metavar=("X", "Y", "Z"))
    parser.add_argument("--camera-look-at", nargs=3, type=float, default=None, metavar=("X", "Y", "Z"))
    parser.add_argument("--smart-sensors", action="store_true", help="Add smart-home motion and pressure sensors.")
    parser.add_argument("--motion-pos", nargs=3, type=float, default=(0.0, -2.0, 1.4), metavar=("X", "Y", "Z"))
    parser.add_argument("--motion-yaw-deg", type=float, default=90.0)
    parser.add_argument("--motion-range", type=float, default=4.0)
    parser.add_argument("--motion-fov-deg", type=float, default=100.0)
    parser.add_argument("--show-motion-fov", action="store_true", help="Draw the motion sensor FOV area. This can look opaque in RTX mode.")
    parser.add_argument("--pressure-pos", nargs=3, type=float, default=(0.0, 0.0, 0.015), metavar=("X", "Y", "Z"))
    parser.add_argument("--pressure-size", nargs=3, type=float, default=(0.9, 0.9, 0.03), metavar=("X", "Y", "Z"))
    parser.add_argument("--pressure-threshold-kg", type=float, default=20.0)
    parser.add_argument("--dummy-weight-kg", type=float, default=70.0)
    parser.add_argument("--laundry-basket", action="store_true", help="Add a laundry basket marker for pressure-sensor tests.")
    parser.add_argument("--laundry-basket-pos", nargs=3, type=float, default=(0.8, -0.2, 0.18), metavar=("X", "Y", "Z"))
    parser.add_argument("--laundry-basket-size", nargs=3, type=float, default=(0.55, 0.55, 0.36), metavar=("X", "Y", "Z"))
    parser.add_argument("--laundry-basket-weight-kg", type=float, default=1.5)
    parser.add_argument("--laundry-load-kg", type=float, default=24.0)
    parser.add_argument("--physical-pressure-demo", action="store_true", help="Spawn rigid laundry pieces whose physical positions drive the pressure value.")
    parser.add_argument("--laundry-piece-mass-kg", type=float, default=3.0)
    parser.add_argument("--laundry-piece-radius", type=float, default=0.09)
    parser.add_argument("--laundry-add-interval", type=float, default=3.0)
    parser.add_argument("--laundry-max-pieces", type=int, default=10)
    parser.add_argument("--laundry-drop-height", type=float, default=1.15)
    parser.add_argument("--single-heavy-load-demo", action="store_true", help="Move one heavy laundry load in and out of the basket repeatedly.")
    parser.add_argument("--heavy-load-mass-kg", type=float, default=8.0)
    parser.add_argument("--heavy-load-radius", type=float, default=0.16)
    parser.add_argument("--heavy-load-cycle", type=float, default=6.0)
    parser.add_argument("--heavy-load-high-z", type=float, default=1.15)
    parser.add_argument("--heavy-load-low-z", type=float, default=0.25)
    parser.add_argument("--stream-sensors", action="store_true", help="Publish sensor readings as UDP JSON packets.")
    parser.add_argument("--stream-host", default="127.0.0.1")
    parser.add_argument("--stream-port", type=int, default=8765)
    parser.add_argument("--sensor-log-interval", type=float, default=1.0)
    parser.add_argument("--sensor-log-path", default=None)
    args = parser.parse_args()

    if args.preset is not None:
        preset = PRESETS[args.preset]
        args.scene = preset["scene"]
        args.full = True
        args.clean_structure_materials = True
        args.dummy = True
        args.smart_sensors = True
        args.laundry_basket = True
        args.physical_pressure_demo = True
        args.single_heavy_load_demo = True
        args.stream_sensors = True
        args.dummy_pos = preset["dummy_pos"]
        args.dummy_move_delta = preset["dummy_move_delta"]
        args.camera_pos = preset["camera_pos"]
        args.camera_look_at = preset["camera_look_at"]
        args.motion_pos = preset["motion_pos"]
        args.motion_yaw_deg = preset["motion_yaw_deg"]
        args.motion_range = preset.get("motion_range", args.motion_range)
        args.motion_fov_deg = preset.get("motion_fov_deg", args.motion_fov_deg)
        args.pressure_pos = preset["pressure_pos"]
        args.laundry_basket_pos = preset["laundry_basket_pos"]

    gm.HEADLESS = False
    gm.USE_GPU_DYNAMICS = True
    gm.ENABLE_FLATCACHE = not args.physical_pressure_demo
    gm.ENABLE_OBJECT_STATES = False
    gm.ENABLE_TRANSITION_RULES = False

    cfg = {
        "scene": {
            "type": "InteractiveTraversableScene",
            "scene_model": args.scene,
        },
        "task": {
            "type": "DummyTask",
        },
    }

    if not args.full:
        cfg["scene"]["load_object_categories"] = list(STRUCTURE_CATEGORIES)

    env = og.Environment(configs=cfg)

    if args.clean_structure_materials:
        _clean_structure_materials()

    dummy_root = None
    if args.dummy:
        dummy_root = _add_dummy_human(position=args.dummy_pos, height=args.dummy_height)

    laundry_basket_root = None
    if args.laundry_basket:
        laundry_basket_root = _add_laundry_basket(
            position=args.laundry_basket_pos,
            size=args.laundry_basket_size,
        )

    heavy_load_prim = None
    if args.single_heavy_load_demo and laundry_basket_root is not None:
        basket_pos = _get_root_position(laundry_basket_root)
        heavy_load_prim = _add_heavy_laundry_load(
            position=(basket_pos[0], basket_pos[1], args.heavy_load_high_z),
            radius=args.heavy_load_radius,
        )

    sensor_rig = None
    if args.smart_sensors:
        sensor_rig = SmartHomeSensorRig(
            motion_position=args.motion_pos,
            motion_yaw_deg=args.motion_yaw_deg,
            motion_range_m=args.motion_range,
            motion_fov_deg=args.motion_fov_deg,
            show_motion_fov=args.show_motion_fov,
            pressure_position=args.pressure_pos,
            pressure_size=args.pressure_size,
            pressure_threshold_kg=args.pressure_threshold_kg,
        )
        if args.physical_pressure_demo:
            _enable_pressure_pad_collision(sensor_rig)

    stream_publisher = None
    if args.stream_sensors:
        stream_publisher = SensorStreamPublisher(udp_host=args.stream_host, udp_port=args.stream_port)

    target = args.camera_look_at
    if target is None:
        target = [args.dummy_pos[0], args.dummy_pos[1], args.dummy_pos[2] + 0.9]

    camera_pos = args.camera_pos
    if camera_pos is None:
        camera_pos = [target[0] - 2.2, target[1] - 2.8, target[2] + 0.6]

    og.sim.viewer_camera.set_position_orientation(
        position=th.tensor(camera_pos, dtype=th.float32),
        orientation=_look_at_quat(camera_pos=camera_pos, target_pos=target),
    )

    cam_mover = og.sim.enable_viewer_camera_teleoperation()
    cam_mover.set_delta(args.camera_speed)

    KeyboardEventHandler.initialize()
    KeyboardEventHandler.add_keyboard_callback(
        key=lazy.carb.input.KeyboardInput.ESCAPE,
        callback_fn=lambda: og.shutdown(),
    )
    if dummy_root is not None:
        _configure_dummy_controls(dummy_root=dummy_root, step=args.dummy_step)

    print(f"Loaded scene: {args.scene}")
    if args.dummy:
        print(f"Added dummy human at {args.dummy_pos} with height {args.dummy_height}m")
        print("Dummy controls: I/K forward-back, J/L left-right, U/M up-down")
    print(f"Camera speed set to {args.camera_speed}")
    if sensor_rig is not None:
        print("Smart-home sensors enabled")
        print(f"Motion sensor: pos={args.motion_pos}, yaw={args.motion_yaw_deg}, range={args.motion_range}m")
        print(f"Pressure sensor: pos={args.pressure_pos}, size={args.pressure_size}, threshold={args.pressure_threshold_kg}kg")
        if args.sensor_log_path:
            print(f"Sensor log path: {args.sensor_log_path}")
    if stream_publisher is not None:
        print(f"Sensor UDP stream: {args.stream_host}:{args.stream_port}")
    if laundry_basket_root is not None:
        print(
            "Laundry basket enabled: "
            f"pos={args.laundry_basket_pos}, basket_weight={args.laundry_basket_weight_kg}kg, "
            f"load={args.laundry_load_kg}kg"
        )
    if heavy_load_prim is not None:
        print(
            "Single heavy load demo enabled: "
            f"mass={args.heavy_load_mass_kg}kg, low_z={args.heavy_load_low_z}, high_z={args.heavy_load_high_z}, "
            f"cycle={args.heavy_load_cycle}s"
        )
    print("Press ESC in the Isaac Sim window to quit.")

    frame = 0
    last_sensor_log_t = -args.sensor_log_interval
    last_laundry_add_t = -args.laundry_add_interval
    laundry_load_pieces = []
    start_pos = th.tensor(args.dummy_pos, dtype=th.float32)
    move_delta = th.tensor(args.dummy_move_delta, dtype=th.float32)
    while True:
        sim_t = frame * og.sim.get_sim_step_dt()
        if dummy_root is not None and args.dummy_autoplay:
            phase = 0.5 * (1.0 - math.cos(sim_t * args.dummy_speed * math.pi))
            _set_dummy_position(dummy_root, (start_pos + move_delta * phase).tolist())

        heavy_load_pos = None
        if heavy_load_prim is not None and laundry_basket_root is not None:
            basket_pos = _get_root_position(laundry_basket_root)
            cycle_phase = 0.5 * (1.0 - math.cos(2.0 * math.pi * sim_t / max(args.heavy_load_cycle, 1e-6)))
            z = args.heavy_load_high_z + (args.heavy_load_low_z - args.heavy_load_high_z) * cycle_phase
            heavy_load_pos = [basket_pos[0], basket_pos[1], z]
            _set_prim_translate(heavy_load_prim, heavy_load_pos)

        if args.physical_pressure_demo and laundry_basket_root is not None and heavy_load_prim is None:
            if len(laundry_load_pieces) < args.laundry_max_pieces and sim_t - last_laundry_add_t >= args.laundry_add_interval:
                basket_pos = _get_root_position(laundry_basket_root)
                offset = len(laundry_load_pieces)
                drop_pos = [
                    basket_pos[0] + 0.04 * ((offset % 3) - 1),
                    basket_pos[1] + 0.04 * (((offset // 3) % 3) - 1),
                    basket_pos[2] + args.laundry_drop_height,
                ]
                piece = _add_laundry_load_piece(
                    index=len(laundry_load_pieces),
                    position=drop_pos,
                    radius=args.laundry_piece_radius,
                    mass_kg=args.laundry_piece_mass_kg,
                )
                piece["spawn_time"] = sim_t
                laundry_load_pieces.append(piece)
                last_laundry_add_t = sim_t

        if sensor_rig is not None and dummy_root is not None:
            dummy_pos = _get_dummy_position(dummy_root).tolist()
            weighted_positions = []
            if heavy_load_pos is not None:
                weighted_positions.extend(
                    _weighted_physical_loads_on_sensor(
                        [{"prim": heavy_load_prim, "mass_kg": args.heavy_load_mass_kg}],
                        pressure_pos=args.pressure_pos,
                        pressure_size=args.pressure_size,
                        sim_time=sim_t,
                    )
                )
            elif args.physical_pressure_demo:
                weighted_positions.extend(
                    _weighted_physical_loads_on_sensor(
                        laundry_load_pieces,
                        pressure_pos=args.pressure_pos,
                        pressure_size=args.pressure_size,
                        sim_time=sim_t,
                    )
                )
            else:
                weighted_positions.append((dummy_pos, args.dummy_weight_kg))
            if laundry_basket_root is not None and not args.physical_pressure_demo:
                basket_pos = _get_root_position(laundry_basket_root)
                weighted_positions.append(
                    (basket_pos, args.laundry_basket_weight_kg + args.laundry_load_kg)
                )
            readings = sensor_rig.read(
                resident_position=dummy_pos,
                weighted_positions=weighted_positions,
            )
            if sim_t - last_sensor_log_t >= args.sensor_log_interval:
                if args.physical_pressure_demo:
                    if heavy_load_pos is not None:
                        is_load_on_sensor = len(
                            _weighted_physical_loads_on_sensor(
                                [{"prim": heavy_load_prim, "mass_kg": args.heavy_load_mass_kg}],
                                pressure_pos=args.pressure_pos,
                                pressure_size=args.pressure_size,
                                sim_time=sim_t,
                            )
                        ) > 0
                        readings["pressure_sensors"]["pressure_sensor_0"]["heavy_load_in_basket"] = is_load_on_sensor
                        readings["pressure_sensors"]["pressure_sensor_0"]["heavy_load_z"] = heavy_load_pos[2]
                    else:
                        readings["pressure_sensors"]["pressure_sensor_0"]["physical_load_piece_count"] = len(
                            _weighted_physical_loads_on_sensor(
                                laundry_load_pieces,
                                pressure_pos=args.pressure_pos,
                                pressure_size=args.pressure_size,
                                sim_time=sim_t,
                            )
                        )
                        readings["pressure_sensors"]["pressure_sensor_0"]["total_spawned_load_pieces"] = len(
                            laundry_load_pieces
                        )
                log_line = f"[smart_home] t={sim_t:.2f} {readings}"
                print(log_line, flush=True)
                if args.sensor_log_path:
                    with open(args.sensor_log_path, "a", encoding="utf-8") as f:
                        f.write(log_line + "\n")
                if stream_publisher is not None:
                    stream_publisher.publish(sim_time=sim_t, readings=readings)
                last_sensor_log_t = sim_t

        frame += 1
        env.step([])


if __name__ == "__main__":
    main()
