import math
from dataclasses import dataclass

import torch as th

import omnigibson as og
import omnigibson.lazy as lazy


MOTION_FOV_OPACITY = 0.13


def _vec3(value):
    return lazy.pxr.Gf.Vec3d(float(value[0]), float(value[1]), float(value[2]))


def _set_display_color(usd_geom, color):
    attr = usd_geom.GetDisplayColorAttr()
    if not attr:
        attr = usd_geom.CreateDisplayColorAttr()
    attr.Set([lazy.pxr.Gf.Vec3f(float(color[0]), float(color[1]), float(color[2]))])


def _set_display_opacity(usd_geom, opacity):
    attr = usd_geom.GetDisplayOpacityAttr()
    if not attr:
        attr = usd_geom.CreateDisplayOpacityAttr()
    attr.Set([float(opacity)])


def _set_visible(usd_geom, visible):
    imageable = lazy.pxr.UsdGeom.Imageable(usd_geom.GetPrim())
    if visible:
        imageable.MakeVisible()
    else:
        imageable.MakeInvisible()


def _add_xform_ops(usd_geom, position, scale=None, orient_z_rad=None):
    xform = lazy.pxr.UsdGeom.Xformable(usd_geom.GetPrim())
    xform.ClearXformOpOrder()
    xform.AddTranslateOp().Set(_vec3(position))
    if orient_z_rad is not None:
        xform.AddRotateZOp().Set(math.degrees(orient_z_rad))
    if scale is not None:
        xform.AddScaleOp().Set(_vec3(scale))


def _get_xform_position(xform):
    value = xform.GetPrim().GetAttribute("xformOp:translate").Get()
    return th.tensor([value[0], value[1], value[2]], dtype=th.float32)


def _get_rotate_z_rad(xform):
    value = xform.GetPrim().GetAttribute("xformOp:rotateZ").Get()
    return math.radians(float(value)) if value is not None else None


@dataclass
class MotionSensorReading:
    name: str
    zone: str
    detected: bool
    target_position: tuple[float, float, float] | None
    distance: float | None


class MotionSensor:
    def __init__(self, name, position, yaw_deg=0.0, range_m=4.0, fov_deg=90.0, show_fov=False, zone=None):
        self.name = name
        self.zone = zone or name
        self.position = th.tensor(position, dtype=th.float32)
        self.yaw_rad = math.radians(yaw_deg)
        self.range_m = float(range_m)
        self.fov_rad = math.radians(fov_deg)
        self.occluders = []
        self.fov_visible = bool(show_fov)
        self.active = True

        root_path = f"/World/smart_home/{name}"
        forward = th.tensor([math.cos(self.yaw_rad), math.sin(self.yaw_rad), 0.0], dtype=th.float32)
        mount_pos = self.position - forward * 0.10
        self.mount = lazy.pxr.UsdGeom.Cube.Define(og.sim.stage, f"{root_path}_wall_mount")
        self.mount.CreateSizeAttr(1.0)
        self.mount_scale = (0.045, 0.18, 0.12)
        _add_xform_ops(self.mount, mount_pos.tolist(), scale=self.mount_scale, orient_z_rad=self.yaw_rad)
        _set_display_color(self.mount, (0.9, 0.92, 0.9))

        self.marker = lazy.pxr.UsdGeom.Cone.Define(og.sim.stage, root_path)
        self.marker.CreateRadiusAttr(0.07)
        self.marker.CreateHeightAttr(0.12)
        _add_xform_ops(self.marker, self.position.tolist(), orient_z_rad=self.yaw_rad)
        _set_display_color(self.marker, (0.88, 0.92, 0.95))
        self.fov_mesh = self._create_fov_mesh(f"{root_path}_range")
        self.set_fov_visible(show_fov)

    def set_active(self, active, fov_visible=None):
        self.active = bool(active)
        _set_visible(self.marker, self.active)
        _set_visible(self.mount, self.active)
        if self.active:
            self.set_fov_visible(self.fov_visible if fov_visible is None else fov_visible)
        else:
            self.set_fov_visible(False)

    def sync_from_stage(self):
        position = _get_xform_position(self.marker)
        yaw_rad = _get_rotate_z_rad(self.marker)
        changed = bool(th.norm(position - self.position) > 1e-4)
        self.position = position
        if yaw_rad is not None and abs(yaw_rad - self.yaw_rad) > 1e-4:
            self.yaw_rad = yaw_rad
            changed = True
        if changed:
            forward = th.tensor([math.cos(self.yaw_rad), math.sin(self.yaw_rad), 0.0], dtype=th.float32)
            mount_pos = self.position - forward * 0.10
            _add_xform_ops(self.mount, mount_pos.tolist(), scale=self.mount_scale, orient_z_rad=self.yaw_rad)
            self._refresh_fov_mesh()

    def export_spec(self):
        self.sync_from_stage()
        return {
            "sensor_id": self.name,
            "zone": self.zone,
            "position": [round(float(value), 4) for value in self.position.tolist()],
            "yaw_deg": round(math.degrees(self.yaw_rad), 3),
            "range_m": round(float(self.range_m), 4),
            "fov_deg": round(math.degrees(self.fov_rad), 3),
        }

    def set_occluders(self, occluders):
        self.occluders = list(occluders or [])
        self._refresh_fov_mesh()

    def _create_fov_mesh(self, prim_path):
        pxr = lazy.pxr
        mesh = pxr.UsdGeom.Mesh.Define(og.sim.stage, prim_path)
        self._set_fov_mesh_points(mesh)
        mesh.CreateDoubleSidedAttr(True)
        mesh.CreateDisplayOpacityAttr([MOTION_FOV_OPACITY])
        _set_display_color(mesh, (0.75, 0.85, 1.0))
        return mesh

    def _fov_points(self):
        pxr = lazy.pxr
        segments = 24
        z = 0.035
        points = [pxr.Gf.Vec3f(float(self.position[0]), float(self.position[1]), z)]
        start = self.yaw_rad - self.fov_rad * 0.5
        for i in range(segments + 1):
            angle = start + self.fov_rad * i / segments
            distance = self._ray_distance_to_occluder(angle)
            points.append(
                pxr.Gf.Vec3f(
                    float(self.position[0]) + math.cos(angle) * distance,
                    float(self.position[1]) + math.sin(angle) * distance,
                    z,
                )
            )
        return points

    def _set_fov_mesh_points(self, mesh):
        points = self._fov_points()
        face_vertex_counts = [len(points)]
        face_vertex_indices = list(range(len(points)))
        points_attr = mesh.GetPointsAttr() or mesh.CreatePointsAttr()
        counts_attr = mesh.GetFaceVertexCountsAttr() or mesh.CreateFaceVertexCountsAttr()
        indices_attr = mesh.GetFaceVertexIndicesAttr() or mesh.CreateFaceVertexIndicesAttr()
        points_attr.Set(points)
        counts_attr.Set(face_vertex_counts)
        indices_attr.Set(face_vertex_indices)

    def _refresh_fov_mesh(self):
        if self.fov_mesh is not None:
            self._set_fov_mesh_points(self.fov_mesh)

    def _collapse_fov_mesh(self):
        if self.fov_mesh is None:
            return
        pxr = lazy.pxr
        point = pxr.Gf.Vec3f(float(self.position[0]), float(self.position[1]), 0.035)
        points = [point, point, point]
        self.fov_mesh.GetPointsAttr().Set(points)
        self.fov_mesh.GetFaceVertexCountsAttr().Set([len(points)])
        self.fov_mesh.GetFaceVertexIndicesAttr().Set(list(range(len(points))))

    def set_fov_visible(self, visible):
        self.fov_visible = bool(visible)
        if self.fov_mesh is not None:
            if visible:
                self._refresh_fov_mesh()
                _set_display_opacity(self.fov_mesh, MOTION_FOV_OPACITY)
                _set_visible(self.fov_mesh, True)
            else:
                _set_display_color(self.fov_mesh, (0.75, 0.85, 1.0))
                _set_display_opacity(self.fov_mesh, 0.0)
                self._collapse_fov_mesh()
                _set_visible(self.fov_mesh, False)

    def _point_inside_box_2d(self, point, occluder):
        x, y = float(point[0]), float(point[1])
        min_pt = occluder["min"]
        max_pt = occluder["max"]
        return min_pt[0] <= x <= max_pt[0] and min_pt[1] <= y <= max_pt[1]

    def _segment_intersects_box_2d(self, start, end, occluder):
        if self._point_inside_box_2d(start, occluder) or self._point_inside_box_2d(end, occluder):
            return False
        x0, y0 = float(start[0]), float(start[1])
        x1, y1 = float(end[0]), float(end[1])
        dx = x1 - x0
        dy = y1 - y0
        t_min = 0.0
        t_max = 1.0
        for axis, origin, direction in ((0, x0, dx), (1, y0, dy)):
            box_min = float(occluder["min"][axis])
            box_max = float(occluder["max"][axis])
            if abs(direction) < 1e-8:
                if origin < box_min or origin > box_max:
                    return False
                continue
            inv = 1.0 / direction
            t1 = (box_min - origin) * inv
            t2 = (box_max - origin) * inv
            t_near = min(t1, t2)
            t_far = max(t1, t2)
            t_min = max(t_min, t_near)
            t_max = min(t_max, t_far)
            if t_min > t_max:
                return False
        return 0.0 < t_min < 1.0

    def _ray_distance_to_occluder(self, angle):
        origin = self.position[:2].tolist()
        direction = (math.cos(angle), math.sin(angle))
        nearest = self.range_m
        for occluder in self.occluders:
            if self._point_inside_box_2d(origin, occluder):
                continue
            t_min = 0.0
            t_max = self.range_m
            valid = True
            for axis, start in enumerate(origin):
                ray = direction[axis]
                box_min = float(occluder["min"][axis])
                box_max = float(occluder["max"][axis])
                if abs(ray) < 1e-8:
                    if start < box_min or start > box_max:
                        valid = False
                        break
                    continue
                inv = 1.0 / ray
                t1 = (box_min - start) * inv
                t2 = (box_max - start) * inv
                t_near = min(t1, t2)
                t_far = max(t1, t2)
                t_min = max(t_min, t_near)
                t_max = min(t_max, t_far)
                if t_min > t_max:
                    valid = False
                    break
            if valid and 0.0 < t_min < nearest:
                nearest = max(0.0, t_min - 0.03)
        return nearest

    def _is_occluded(self, target):
        start = self.position[:2].tolist()
        end = target[:2].tolist()
        return any(self._segment_intersects_box_2d(start, end, occluder) for occluder in self.occluders)

    def read(self, target_position):
        self.sync_from_stage()
        target = th.tensor(target_position, dtype=th.float32)
        delta = target - self.position
        horizontal = delta[:2]
        distance = float(th.norm(horizontal))

        detected = False
        if 0.001 < distance <= self.range_m:
            forward = th.tensor([math.cos(self.yaw_rad), math.sin(self.yaw_rad)], dtype=th.float32)
            direction = horizontal / th.norm(horizontal)
            dot = float(th.clamp(th.dot(forward, direction), -1.0, 1.0))
            angle = math.acos(dot)
            detected = angle <= self.fov_rad * 0.5 and not self._is_occluded(target)

        _set_display_color(self.marker, (0.0, 1.0, 0.2) if detected else (0.88, 0.92, 0.95))
        if self.fov_mesh is not None and self.fov_visible:
            _set_display_color(self.fov_mesh, (0.0, 1.0, 0.2) if detected else (0.75, 0.85, 1.0))
        return MotionSensorReading(
            name=self.name,
            zone=self.zone,
            detected=detected,
            target_position=tuple(float(x) for x in target.tolist()) if detected else None,
            distance=distance if detected else None,
        )


@dataclass
class PressureSensorReading:
    name: str
    triggered: bool
    estimated_weight_kg: float


class PressureSensor:
    def __init__(self, name, position, size=(0.8, 0.8, 0.03), threshold_kg=10.0, show_visual=True):
        self.name = name
        self.position = th.tensor(position, dtype=th.float32)
        self.size = th.tensor(size, dtype=th.float32)
        self.threshold_kg = float(threshold_kg)
        self.gauge_height_m = 1.0
        self.show_visual = bool(show_visual)
        self.marker = None
        self.gauge_back = None
        self.gauge_fill = None

        if not self.show_visual:
            return

        root_path = f"/World/smart_home/{name}"
        self.marker = lazy.pxr.UsdGeom.Cube.Define(og.sim.stage, root_path)
        self.marker.CreateSizeAttr(1.0)
        _add_xform_ops(self.marker, self.position.tolist(), scale=self.size.tolist())
        _set_display_color(self.marker, (0.1, 0.45, 1.0))

        self.gauge_back = lazy.pxr.UsdGeom.Cube.Define(og.sim.stage, f"{root_path}_gauge_back")
        self.gauge_back.CreateSizeAttr(1.0)
        self.gauge_position = (
            float(self.position[0]),
            float(self.position[1]) - 0.75,
            self.gauge_height_m * 0.5,
        )
        _add_xform_ops(self.gauge_back, self.gauge_position, scale=(0.12, 0.12, self.gauge_height_m))
        _set_display_color(self.gauge_back, (0.15, 0.15, 0.15))

        self.gauge_fill = lazy.pxr.UsdGeom.Cube.Define(og.sim.stage, f"{root_path}_gauge_fill")
        self.gauge_fill.CreateSizeAttr(1.0)
        _add_xform_ops(
            self.gauge_fill,
            (self.gauge_position[0], self.gauge_position[1], 0.02),
            scale=(0.16, 0.16, 0.04),
        )
        _set_display_color(self.gauge_fill, (0.1, 0.45, 1.0))

    def read(self, weighted_positions):
        total_weight = 0.0
        half = self.size[:2] * 0.5
        for position, weight_kg in weighted_positions:
            pos = th.tensor(position, dtype=th.float32)
            rel = pos[:2] - self.position[:2]
            if abs(float(rel[0])) <= float(half[0]) and abs(float(rel[1])) <= float(half[1]):
                total_weight += float(weight_kg)

        triggered = total_weight >= self.threshold_kg
        if not self.show_visual:
            return PressureSensorReading(
                name=self.name,
                triggered=triggered,
                estimated_weight_kg=total_weight,
            )

        color = (1.0, 0.2, 0.05) if triggered else (0.1, 0.45, 1.0)
        _set_display_color(self.marker, color)
        fill_height = max(0.04, min(self.gauge_height_m, self.gauge_height_m * total_weight / max(self.threshold_kg, 1e-6)))
        _add_xform_ops(
            self.gauge_fill,
            (self.gauge_position[0], self.gauge_position[1], fill_height * 0.5),
            scale=(0.16, 0.16, fill_height),
        )
        _set_display_color(self.gauge_fill, color)
        return PressureSensorReading(
            name=self.name,
            triggered=triggered,
            estimated_weight_kg=total_weight,
        )


class SmartHomeSensorRig:
    def __init__(
        self,
        motion_position=(0.0, -2.0, 1.4),
        motion_yaw_deg=90.0,
        motion_range_m=4.0,
        motion_fov_deg=100.0,
        motion_sensors=None,
        show_motion_fov=False,
        pressure_position=(0.0, 0.0, 0.015),
        pressure_name="pressure_sensor_0",
        pressure_size=(0.9, 0.9, 0.03),
        pressure_threshold_kg=20.0,
        show_pressure_visual=True,
    ):
        lazy.pxr.UsdGeom.Xform.Define(og.sim.stage, "/World/smart_home")
        specs = motion_sensors or [
            {
                "name": "motion_sensor_0",
                "zone": "sensor_area",
                "position": motion_position,
                "yaw_deg": motion_yaw_deg,
                "range_m": motion_range_m,
                "fov_deg": motion_fov_deg,
            }
        ]
        self.motion_sensors = []
        self.motion_sensor_by_name = {}
        for spec in specs:
            sensor = MotionSensor(
                name=spec["name"],
                zone=spec.get("zone"),
                position=spec["position"],
                yaw_deg=spec.get("yaw_deg", 0.0),
                range_m=spec.get("range_m", 4.0),
                fov_deg=spec.get("fov_deg", 90.0),
                show_fov=show_motion_fov,
            )
            self.motion_sensors.append(sensor)
            self.motion_sensor_by_name[sensor.name] = sensor
        self.active_motion_sensor_names = [sensor.name for sensor in self.motion_sensors]
        self.motion_fov_visible = bool(show_motion_fov)
        self.pressure_sensor = PressureSensor(
            name=pressure_name,
            position=pressure_position,
            size=pressure_size,
            threshold_kg=pressure_threshold_kg,
            show_visual=show_pressure_visual,
        )

    def set_motion_fov_visible(self, visible):
        self.motion_fov_visible = bool(visible)
        for sensor in self.motion_sensors:
            sensor.set_fov_visible(self.motion_fov_visible if sensor.name in self.active_motion_sensor_names else False)

    def set_active_motion_sensors(self, names):
        requested = [str(name) for name in names or []]
        active = [name for name in requested if name in self.motion_sensor_by_name]
        if not active:
            active = [sensor.name for sensor in self.motion_sensors]
        self.active_motion_sensor_names = active
        active_set = set(active)
        for sensor in self.motion_sensors:
            sensor.set_active(sensor.name in active_set, self.motion_fov_visible)
        return list(self.active_motion_sensor_names)

    def export_active_motion_sensor_specs(self):
        active = set(self.active_motion_sensor_names)
        return [sensor.export_spec() for sensor in self.motion_sensors if sensor.name in active]

    def set_motion_occluders(self, occluders):
        for sensor in self.motion_sensors:
            sensor.set_occluders(occluders)

    def read(self, resident_position, weighted_positions=()):
        active = set(self.active_motion_sensor_names)
        motion_readings = [sensor.read(resident_position) for sensor in self.motion_sensors if sensor.name in active]
        pressure = self.pressure_sensor.read(weighted_positions)
        return {
            "motion_sensors": {
                motion.name: {
                    "zone": motion.zone,
                    "detected": motion.detected,
                    "target_position": motion.target_position,
                    "distance": motion.distance,
                }
                for motion in motion_readings
            },
            "pressure_sensors": {
                pressure.name: {
                    "triggered": pressure.triggered,
                    "estimated_weight_kg": pressure.estimated_weight_kg,
                }
            },
        }
