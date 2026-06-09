# HomeSense Episode Data Schema

This document describes the episode dataset structure used by the HomeSense OmniGibson demo.

## Goal

The dataset is designed for smart-home robot service development. It keeps the current sensor/context workflow usable while leaving room for future resident-aware collision avoidance and human-aware planning labels.

## Run Directory

Each simulator launch creates a run directory:

```text
logs/homesense_episodes/run_<UTC_TIMESTAMP>/
  metadata.json
  manifest.json
  events.jsonl
  steps.jsonl
  annotations.json
  quality_report.json
  cameras/
    top/
    robot/
```

Legacy flat logs are stored under:

```text
logs/homesense_episodes/legacy/
  episodes_<UTC_TIMESTAMP>.jsonl
  run_<OLD_TIMESTAMP>/
  LEGACY_MANIFEST.json
```

## Metadata

`metadata.json` and `manifest.json` currently contain the same run-level metadata:

- schema version
- scene model and scene variant
- robot type
- resident zone mode
- sensor layout options
- step logging rate
- camera logging settings
- supported data modalities
- training scope
- file layout

## Steps

`steps.jsonl` is the primary training/analysis stream. Each line is a full multimodal snapshot.

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
  "run_dir": ".../logs/homesense_episodes/run_<UTC_TIMESTAMP>",
  "camera_frames": {
    "top": null,
    "robot": {
      "front_camera": {
        "available": true,
        "source": "robot",
        "camera_name": "front_camera",
        "path": "cameras/robot/front_camera/episode_0001_frame_00001234_000001.jpg",
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
        "path": "cameras/robot/left_wrist_camera/episode_0001_frame_00001234_000001.jpg"
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
- The image file itself is stored as JPEG under `cameras/<source>/`; JSONL stores only the relative path and metadata.

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

Future work should compute this from `steps.jsonl` after each run.
