# Smart Home Live Demo Handoff

This document summarizes the current development state for the smart-home robot subscription service validation demo.
It is intended as a handoff note for continuing development in another Codex session or terminal.

## Project Goal

Build a browser-controlled validation app connected to a live OmniGibson / Isaac Sim digital twin.

Target capabilities:

- Stream a live simulator camera view into the browser.
- Switch between scene camera and robot camera from the browser.
- Move the resident marker with keyboard / UI controls.
- Recompute smart-home sensor readings in real time from the resident position and scene state.
- Update active sensor, estimated resident location, and robot context metadata.
- Select replay placeholders for robot tasks.
- Disable resident movement while a robot task replay is running.

## Important Constraints

- Do not download the full BEHAVIOR dataset. It is roughly 2.2 TB.
- Only the needed scene subset and robot assets should be used.
- Current state uses `--empty-scene` because the required BEHAVIOR scene assets are not present.
- Current robot placeholder is OmniGibson `R1Pro`, matching the Galaxea R1 / R1 Pro direction until the collaborator-provided asset and replay data are imported.
- The official OmniGibson robot assets include `r1` and `r1pro`.

## Environment

Repository root:

```bash
/home/user/Projects/csp-2026-k
```

BEHAVIOR checkout:

```bash
/home/user/Projects/csp-2026-k/BEHAVIOR-1K
```

Isaac Sim:

```bash
/home/user/Desktop/isaac-sim-5.1
```

Use Isaac Python:

```bash
/home/user/Desktop/isaac-sim-5.1/python.sh
```

Installed / configured:

- BEHAVIOR-1K branch: `main`
- OmniGibson and BDDL installed against Isaac Sim 5.1 Python.
- OmniGibson robot assets downloaded under `BEHAVIOR-1K/datasets/omnigibson-robot-assets`.
- `r1` and `r1pro` assets exist under robot assets.
- `datasets/behavior-1k-assets/scenes` is not present.

## Current Run Command

From `BEHAVIOR-1K`:

```bash
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/run_live_control_scene.py \
  --empty-scene \
  --preset wider_house \
  --robot-type R1Pro \
  --host 0.0.0.0 \
  --port 8080 \
  --video-fps 8
```

Known access URLs:

```text
http://10.32.253.88:8080/
http://10.48.63.249:8080/
http://127.0.0.1:8080/
```

Check whether the server is running:

```bash
pgrep -af 'run_live_control_scene|uvicorn|omnigibson_5_1_0|python.sh'
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/state
```

## Main Files

Live OmniGibson runner:

```text
examples/smart_home/run_live_control_scene.py
```

FastAPI control and streaming server:

```text
services/control_server/live_scene_app.py
```

Browser UI:

```text
services/control_server/static/live.html
services/control_server/static/live.js
services/control_server/static/live.css
```

Smart-home sensor package copied into BEHAVIOR:

```text
smart_home/
```

Original source package remains at:

```text
/home/user/Projects/csp-2026-k/sensor-implementation-package-main
```

## Implemented Behavior

### OmniGibson Runner

`run_live_control_scene.py` currently:

- Creates an OmniGibson environment.
- Supports `--empty-scene` to avoid loading missing BEHAVIOR scene assets.
- Loads `R1Pro` as the current robot placeholder.
- Creates a simple floor, dummy resident, laundry basket / load marker, and smart-home sensor rig.
- Starts the FastAPI server in a background thread.
- Processes browser commands through a queue in the OmniGibson main loop.
- Uses `og.sim.editing_usd()` for USD mutation paths.
- Captures live camera frames and sends JPEG bytes to the FastAPI bridge.

Command queue is important. FastAPI request handlers must not directly edit USD because that can collide with simulation stepping and raise:

```text
Cannot edit USD while simulation is stepping!
```

### Server Endpoints

`live_scene_app.py` exposes:

- `GET /`
- `GET /health`
- `GET /state`
- `GET /video.mjpg`
- `WS /ws`
- `POST /command/move-human-delta`
- `POST /command/set-camera`
- `POST /command/set-video-source`
- `POST /command/run-task`
- `POST /command/reset`

`/video.mjpg` is a multipart MJPEG stream backed by the latest JPEG frame generated inside the simulator loop.

### Browser UI

The UI currently includes:

- Live MJPEG camera panel.
- `Scene Camera` / `Robot Camera` video source selection.
- Scene camera preset buttons:
  - `Overview`
  - `Living Room`
  - `Laundry`
  - `Robot`
  - `Resident`
- Resident movement controls:
  - Arrow keys
  - WASD
  - On-screen direction buttons
- Sensor status display.
- Robot task buttons:
  - `Deliver Item`
  - `Laundry`
- Event log.

## Validated

The following were verified locally:

- `/health` returns `{"status":"ok","mode":"omnigibson-live"}`.
- `/state` returns live state.
- `/video.mjpg` returns multipart JPEG data.
- `state.video.available` becomes `true`.
- `state.video.frame_id` increments.
- `POST /command/set-video-source` switches from `viewer` to `robot`.
- Resident movement command is queued and processed in the simulator loop.
- Resident position changes are reflected in `/state`.
- Pressure sensor value and robot context metadata update from live scene state.
- `deliver_item` task maps to replay placeholder `delivery_living_room`.
- `laundry` task maps to replay placeholder `laundry_basket_to_washer` when pressure trigger is true.
- Resident movement is blocked while a robot task is running.
- Camera preset switching works.

## Current Limitations

- The current scene is an empty placeholder scene, not the real apartment / home scene.
- The browser stream is MJPEG, not Omniverse Streaming.
- MJPEG is suitable for validation demos but may need tuning for frame rate, resolution, and quality.
- `Robot Camera` depends on the loaded robot exposing an RGB sensor. The current R1Pro placeholder exposes a usable RGB observation in this setup.
- Replay execution is still placeholder selection metadata, not real Galaxea R1 replay playback.
- Real replay integration depends on collaborator-provided Galaxea R1 data and asset import.

## Why Omniverse Streaming Is Not Required

The demo does not need official Omniverse browser streaming if the goal is:

- show a live camera view,
- send controls from browser to simulator,
- display sensor and robot state.

The implemented alternative is:

1. Read simulator camera RGB frames using OmniGibson sensors.
2. Encode frames to JPEG.
3. Serve them as MJPEG through FastAPI.
4. Display them in the browser with an `<img>` element.

This avoids depending on Omniverse Streaming / WebRTC support.

## Dataset Handling

Do not run the full BEHAVIOR asset downloader.

Merom scene-subset acquisition has been completed locally:

- `datasets/omnigibson.key`
- `datasets/behavior-1k-assets/VERSION`
- `datasets/behavior-1k-assets/metadata`
- `datasets/behavior-1k-assets/systems`
- `datasets/behavior-1k-assets/scenes/Merom_0_int`
- `datasets/behavior-1k-assets/objects`
- `merom_0_int_asset_subset_manifest.txt`
- `create_merom_asset_subset.py`

Validation results:

- Manifest entries: 74
- Missing manifest entries: 0
- Object model directories: 69
- `Merom_0_int_best.json` parses successfully.
- `Merom_0_int_best.json` contains 104 scene object init entries.
- All 104 scene object init entries match local object model directories.
- OmniGibson recognizes `Merom_0_int` through `get_available_behavior_1k_scenes()`.
- Smoke test successfully imported the scene and started the live server on `127.0.0.1:8090`.
- `/health`, `/state`, and `/video.mjpg` responded during the smoke test.

Run with the actual Merom scene:

```bash
cd /home/user/Projects/csp-2026-k/BEHAVIOR-1K
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/run_live_control_scene.py \
  --scene-model Merom_0_int \
  --robot-type R1Pro \
  --host 0.0.0.0 \
  --port 8080 \
  --video-fps 8
```

The live runner now supports `--scene-model` to override the preset scene.

Historical scene-subset acquisition notes:

- Choose the target scene, for example `Merom_0_int`.
- Obtain only the needed scene directory and referenced assets.
- Place the subset under the expected BEHAVIOR dataset structure.
- Run without `--empty-scene` once the subset exists.
- Use the local scene-subset helper instead of `setup.sh --dataset`:

```bash
cd /home/user/Projects/csp-2026-k/BEHAVIOR-1K
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/download_behavior_scene_assets.py --check-access
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/download_behavior_scene_assets.py
```

- If the access check reports no Hugging Face token, the user must run:

```bash
/home/user/Desktop/isaac-sim-5.1/python.sh -c "from huggingface_hub import login; login()"
```

- The helper initially downloads only `scenes/**`, `metadata/**`, and `VERSION`.
- If `Merom_0_int` load fails due to missing referenced objects or systems, add those explicit patterns rather than downloading the full dataset.

Expected missing path today:

```text
BEHAVIOR-1K/datasets/behavior-1k-assets/scenes
```

## Next Development Steps

1. Add explicit video quality / resolution controls.
   - `--video-fps` already exists.
   - Add JPEG quality CLI option.
   - Consider lower frame resolution if CPU load is high.

2. Improve video source fallback.
   - If robot camera is unavailable, keep viewer source active and surface a UI event.
   - Show clear source status in `/state.video`.

3. Polish UI.
   - Add a loading state for the MJPEG stream.
   - Compact event log.
   - Show task running countdown.

4. Integrate real scene subset.
   - Do not download all 2.2 TB.
   - Use only needed scene and referenced object / texture assets.
   - Remove `--empty-scene` once scene assets are present.

5. Integrate real replay data.
   - Replace placeholder replay registry entries with collaborator-provided Galaxea R1 replay metadata.
   - Map task requests to imported replay trajectories.
   - Feed robot context metadata to replay selection, even if replay itself is pre-recorded.

## Troubleshooting

If port 8080 is occupied:

```bash
pgrep -af 'run_live_control_scene|uvicorn|python.sh'
```

Stop the old run if needed:

```bash
pkill -f 'run_live_control_scene.py'
```

If USD edit errors appear:

- Do not mutate USD from FastAPI request handlers.
- Queue commands and process them in the OmniGibson main loop.
- Wrap USD mutations in `with og.sim.editing_usd():`.

If `behavior-1k-assets/scenes` is missing:

- Use `--empty-scene`.
- Do not run the full dataset downloader.
