# Scene Profiles

Scene profiles keep Digital Twin demo logic scene-swappable without making the
robot policy itself multi-scene. The current robot movement demo is still
validated only on `Merom_0_int`; additional scenes should add a profile here and
start with sensor/resident context validation before robot rollout.

## Current Profiles

- `merom_0_int.yaml`: primary HomeSense digital-twin demo scene.
- `house_double_floor_lower.yaml`: temporary replay scene profile for
  `vr_demo_test_v01.hdf5` / `turning_on_radio`. This profile was created from
  the replay task JSON before the actual scene asset subset arrived, so sensor
  and resident positions should be visually revalidated after downloading
  `datasets/behavior-1k-assets/scenes/house_double_floor_lower`.

## Required Fields

- `scene_model`: BEHAVIOR / OmniGibson scene model name.
- `resident.start_position`: default resident spawn pose for reset and startup.
- `zones`: semantic areas used by sensors, logs, and future episode randomizers.
- `motion_sensors`: scene-specific installed motion sensor slots.
- `pressure_sensors`: optional pressure sensor slots.

## Optional Fields

- `overview_camera`: fixed camera pose or `auto_from_scene_bounds: true`.
- `doorless_scene_file`: generated JSON variant for scenes with removed doors.
- `doorless_portals`: passable wall-clearance disks matching removed interior doors.
- `door_object_names`: exact object names for deprecated runtime door hiding.
- `ceiling_model_ids`: scene-specific ceiling prim names to hide in overview.
- `encoder.zone_order`: stable zone order for sensor vector encoding.

## Extension Rule

For a new scene, create:

```text
smart_home/configs/scenes/<scene_model_lowercase>.yaml
```

Then run with:

```bash
python examples/smart_home/run_live_control_scene.py --scene-model <SceneModel> --full --cpu-dynamics
```

If no profile exists, the demo still launches with preset fallback values, but
zone mapping, sensor placement, resident start position, and doorless portal
logic are not scene-specific.
