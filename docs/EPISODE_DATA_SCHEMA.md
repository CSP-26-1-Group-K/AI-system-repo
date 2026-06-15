# HomeSense Episode Data Schema

This document describes the episode dataset structure used by the HomeSense OmniGibson demo.

## Goal

The dataset is designed for smart-home robot service development. It keeps the current sensor/context workflow usable while leaving room for future resident-aware collision avoidance and human-aware planning labels.

## Dataset Units

The dataset distinguishes the OmniGibson process from training samples:

- `session`: one OmniGibson launch. It only stores session-level metadata and event index files.
- `run`: one task replay execution. A run starts when the user presses `T` and ends when the replay completes or is interrupted by `R` / `I`.

This means pressing `N` only prepares an independent resident/context scenario. It does not create a training run by itself.

Each task replay creates a run directory:

```text
datasets/homesense_episodes/
  session_<UTC_TIMESTAMP>_metadata.json
  session_<UTC_TIMESTAMP>_events.jsonl
  run_<UTC_TIMESTAMP>_ep####_<scenario>_<replay_id>/
    metadata/
      metadata.json
      manifest.json
      events.jsonl
      annotations.json
      quality_report.json
      dataset.hdf5
    data/
      steps.jsonl
      cameras/
        <robot_camera_name>/
```

Legacy flat logs are stored under:

```text
datasets/homesense_episodes/legacy/
  episodes_<UTC_TIMESTAMP>.jsonl
```

## Metadata

`metadata/metadata.json` and `metadata/manifest.json` currently contain the same run-level metadata:

- schema version
- scene model and scene variant
- robot type
- task name and replay id
- resident scenario, position, posture, and activity context
- resident zone mode
- sensor layout options
- step logging rate
- camera logging settings
- supported data modalities
- training scope
- file layout

## HDF5 Export

When a run closes, either because replay completed or because the user reset/interrupted with `R` / `I`, the logger also writes:

```text
metadata/dataset.hdf5
```

This file is a structured training index for the run. It does not duplicate JPEG image bytes by default, because that would quickly inflate the dataset. Instead, it stores the camera frame table with relative paths pointing to `data/cameras/<robot_camera_name>/*.jpg`.

Current HDF5 layout:

```text
metadata/dataset.hdf5
  attrs:
    schema_version = homesense_task_run_hdf5_v1
    run_id
    source_run_dir
    image_storage = external_jpeg_relative_paths
  metadata/
    metadata
    manifest
    annotations
    quality_report
  records/
    events_json
    steps_json
  arrays/
    sim_time_s
    wall_time_s
    robot_position
    robot_orientation_xyzw
    resident_position
    resident_velocity
    action_vector
  cameras/
    frames_json
    path
    camera_name
    source
    step_index
    sim_time_s
```

The JSON datasets preserve full fidelity for analysis. The numeric arrays provide a faster path for simple training loaders and sanity checks.

## Steps

`data/steps.jsonl` is the primary training/analysis stream. Each line is a full multimodal snapshot sampled while a task replay is running.

Important top-level fields:

- `schema_version`: currently `homesense_step_v1`
- `episode_id`
- `episode_seed`
- `episode_zone`
- `scenario_type`
- `frame`
- `sim_time_s`
- `wall_time_s`
- `dataset`
- `resident`
- `sensors`
- `robot`
- `robot_pose`
- `action`
- `objects`
- `safety`
- `activity_context`
- `ground_truth`
- `estimates`
- `training_validity`

The legacy-compatible fields `human`, `motion`, `pressure`, `risk`, and `sensor_quality` are still preserved for older scripts.

### Dataset / Camera Frames

Each step includes:

```json
"dataset": {
  "run_dir": ".../datasets/homesense_episodes/run_<UTC_TIMESTAMP>",
  "camera_frames": {
    "top": null,
    "robot": {
      "front_camera": {
        "available": true,
        "source": "robot",
        "camera_name": "front_camera",
        "path": "data/cameras/front_camera/episode_0001_frame_00001234_000001.jpg",
        "frame": 1234,
        "sim_time_s": 41.13,
        "sequence": 1,
        "width": 640,
        "height": 480,
        "format": "jpeg",
        "quality": 80,
        "bytes": 82341
      },
      "left_wrist_camera": {
        "available": true,
        "source": "robot",
        "camera_name": "left_wrist_camera",
        "path": "data/cameras/left_wrist_camera/episode_0001_frame_00001234_000001.jpg"
      }
    }
  }
}
```

Camera frames are sampled at a lower rate than simulator frames to avoid excessive disk writes. Missing or skipped frames are explicit records such as:

```json
{"available": false, "reason": "viewer_not_in_overview_mode"}
```

Current behavior:

- Step records include `episode_phase`: `context_init`, `task_running`, `task_post`, or `task_finished`.
- Camera frames are saved only during `task_running`, so context initialization does not create image samples.
- Robot camera frames come from every RGB sensor discovered on the robot and are grouped by camera name under the same step timestamp.
- Top frames use the active viewer camera only while the viewport mode is `overview`; this avoids moving the user's camera during data collection.
- The image file itself is stored as JPEG under `data/cameras/<camera_name>/`; JSONL stores only the relative path and metadata.

## Resident Section

The `resident` section includes both estimated state and ground truth:

- estimated resident state from the live service state
- ground-truth position
- ground-truth zone
- heading
- velocity
- activity id
- posture

Velocity is currently derived from manual input and is zero for static activity contexts. This keeps the schema ready for future resident trajectory generation.

## Sensor Section

The `sensors` section records:

- raw smart-home sensor readings
- selected motion state
- pressure state
- active sensor layout
- sensor quality diagnostics
- virtual sensor values

Sensor layout variation is a core part of the dataset because smart-home service behavior depends on what the environment can infer before the robot acts.

## Action Section

The `action` section records the latest robot control source:

- `zero`
- `hdf5_replay`
- future `policy`
- future `teleop`

For HDF5 replay, the action vector is compacted so JSONL files remain readable. Full-fidelity action export can be added later if needed.

## Object Section

The `objects` section records task-relevant object states:

- availability
- position
- orientation
- held-by placeholder
- on-floor estimate
- distance-to-goal placeholder

The current implementation records demo objects such as `medicine_bottle_0`.

## Safety Section

The `safety` section is intentionally forward-looking:

- robot-resident distance
- near-collision flag
- collision flag
- resident-in-robot-path placeholder
- robot-should-yield placeholder
- future human-aware planning label

These fields are not yet sufficient for collision avoidance training, but they make the dataset extensible.

## Annotations

`annotations.json` starts with nullable fields:

- `success`
- `failure_reason`
- `annotated_by`
- `notes`

The first implementation supports manual or semi-automatic quality annotation.

## Quality Report

`quality_report.json` starts as an in-progress summary and is updated when an episode closes.

Current fields include:

- episode count
- step count
- min robot-resident distance
- task state flags
- camera frame counts and missing frame ratio
- missing state ratio placeholder

The same final quality report is embedded into `metadata/dataset.hdf5` under `metadata/quality_report`.
