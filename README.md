# CSP-2026-K AI System Repository

This repository tracks the shareable documents and HomeSense prototype code snapshot for Team K's smart-home robot / digital-twin data generation project.

## Contents

- `docs/`
  - project status
  - presentation drafts
  - network stack Q&A notes
  - sensor placement notes
  - latest presentation PDF
- `homesense-demo/`
  - HomeSense live demo code snapshot copied from the local BEHAVIOR-1K working tree
  - OmniGibson viewport-first demo runner
  - optional FastAPI live server
  - smart-home sensor logic
  - legacy Electron client
  - compatibility runner files

## Not Included

The following are intentionally excluded from Git:

- BEHAVIOR-1K upstream repository
- OmniGibson / Isaac Sim datasets
- Merom scene asset bundles
- collaborator export archives
- runtime logs
- `node_modules`
- local crash dumps and generated files

Large assets and scene subsets must be shared separately.

## Current Prototype Summary

The current prototype focuses on an OmniGibson / Isaac Sim viewport demo using the `Merom_0_int` digital twin. It supports resident movement, scene camera switching, sensor range visualization, scene-profile based resident randomization, and JSONL episode logging for future replay / policy training.

The Electron/Web path is preserved as a legacy optional monitoring path, but it is no longer the primary demo surface.

Current source-of-truth document:

- `docs/PROJECT_PROGRESS_2026-05-30.md`

See also `docs/NETWORK_TECH_STACK_QA.md` for presentation Q&A notes.

## Running the OmniGibson Demo Locally

This Git repository does not include the full `BEHAVIOR-1K/` working tree or datasets. Run the demo from the local project root where `BEHAVIOR-1K/`, Isaac Sim, and the required scene assets are already installed.

From `/home/user/Projects/csp-2026-k/BEHAVIOR-1K`:

```bash
TORCHDYNAMO_DISABLE=1 /home/user/Desktop/isaac-sim-5.1/python.sh \
  examples/smart_home/run_live_control_scene.py \
  --scene-model Merom_0_int \
  --full \
  --cpu-dynamics \
  --robot-type R1Pro \
  --hdf5-replay /home/user/Projects/csp-2026-k/delivery_med_room_1_v01.hdf5 \
  --enable-activity-sensors \
  --sensor-layout current \
  --step-log-hz 2.0
```

Sensor layout options:

- `--sensor-layout current`: current deployment layout with added piano-area coverage
- `--sensor-layout dense`: higher coverage layout with fewer blind spots
- `--sensor-layout sparse`: reduced sensor layout for low-coverage / dropout-like data
- While OmniGibson is running, press `L` to cycle `current -> dense -> sparse`.

Viewport controls:

- `N`: start a new randomized resident/context episode
- `T`: run the delivery replay task
- `1`: top overview camera
- `2`: resident follow camera
- `3`: free viewer camera
- `F`: toggle motion sensor range visualization
- `L`: cycle the active motion sensor layout
- `K`: export the active sensor layout after moving sensor prims in the OmniGibson viewport
- `W/A/S/D`: move resident when movement is enabled
- `Q/E`: rotate resident in resident-follow mode

Generated dataset logs are written under:

```text
BEHAVIOR-1K/logs/homesense_episodes/
```

Each run creates an `episodes_<timestamp>.jsonl` event summary and a matching `run_<timestamp>/` directory containing `manifest.json`, `events.jsonl`, and `steps.jsonl`.
