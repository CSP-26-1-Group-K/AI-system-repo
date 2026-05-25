# CSP-2026-K Project Status

Last updated: 2026-05-24

## Project Goal

This project is a validation demo application for a smart-home robot subscription service.

The target demo is a monitoring/control app connected to an OmniGibson digital twin. The current implementation supports both the original browser client and a new Electron desktop client. The app should show the live simulator view, expose smart-home sensor state, allow controlled resident movement, and pass contextual information to the robot task/replay layer.

## Current Target Scene And Robot

- Target scene: `Merom_0_int`
- Placeholder robot asset: `R1Pro`
- Future robot asset: Galaxea R1, once collaborator-provided asset/replay data is imported
- Simulator stack:
  - BEHAVIOR-1K under `BEHAVIOR-1K/`
  - Isaac Sim at `/home/user/Desktop/isaac-sim-5.1`
  - OmniGibson running through `/home/user/Desktop/isaac-sim-5.1/python.sh`

## Current Implementation Locations

- Live demo implementation package:
  - `BEHAVIOR-1K/smart_home/live/runner.py`
  - `BEHAVIOR-1K/smart_home/live/server.py`
  - `BEHAVIOR-1K/smart_home/live/avatar.py`
  - `BEHAVIOR-1K/smart_home/live/constants.py`
  - `BEHAVIOR-1K/smart_home/live/media.py`
  - `BEHAVIOR-1K/smart_home/live/static/live.html`
  - `BEHAVIOR-1K/smart_home/live/static/live.css`
  - `BEHAVIOR-1K/smart_home/live/static/live.js`
- Desktop client:
  - `BEHAVIOR-1K/clients/homesense-electron/package.json`
  - `BEHAVIOR-1K/clients/homesense-electron/src/main.js`
  - `BEHAVIOR-1K/clients/homesense-electron/src/preload.js`
  - `BEHAVIOR-1K/clients/homesense-electron/src/renderer/index.html`
  - `BEHAVIOR-1K/clients/homesense-electron/src/renderer/styles.css`
  - `BEHAVIOR-1K/clients/homesense-electron/src/renderer/app.js`
- Compatibility runner:
  - `BEHAVIOR-1K/examples/smart_home/run_live_control_scene.py`
- Compatibility FastAPI import:
  - `BEHAVIOR-1K/services/control_server/live_scene_app.py`
- Scene subset downloader helper:
  - `BEHAVIOR-1K/examples/smart_home/download_behavior_scene_assets.py`
- Imported asset subset manifest/helper:
  - `BEHAVIOR-1K/merom_0_int_asset_subset_manifest.txt`
  - `BEHAVIOR-1K/create_merom_asset_subset.py`
- Detailed developer handoff:
  - `BEHAVIOR-1K/docs/smart_home_live_demo_handoff.md`

## Completed Work

1. Read the original sensor implementation package README/docs and identified the sensor/control-server architecture.
2. Installed and configured BEHAVIOR-1K / OmniGibson for the local Isaac Sim 5.1 environment.
3. Confirmed OmniGibson robot assets are available locally, including `R1Pro`.
4. Built a live browser demo server around OmniGibson:
   - `/health`
   - `/state`
   - `/video.mjpg`
   - `/ws`
   - movement/task/camera command endpoints
5. Implemented MJPEG live simulator streaming to the browser.
6. Implemented browser controls for:
   - scene camera vs robot camera video source
   - camera preset switching
   - keyboard resident movement
   - task buttons
   - sensor and robot context monitoring
7. Implemented task-running state so resident movement is blocked while the robot is busy.
8. Avoided full BEHAVIOR dataset download. Full dataset/raw/demo data is too large and must not be downloaded casually.
9. Added a scene-subset downloader helper for Hugging Face-based selective asset downloads, but local HF access was unavailable.
10. Chose USB/manual transfer path for Merom subset.
11. Imported the transferred `Merom_0_int` asset subset.
12. Verified the imported subset:
    - Manifest entries: 74
    - Missing manifest entries: 0
    - Object model directories: 69
    - `Merom_0_int_best.json` parses successfully.
    - Scene object init entries: 104
    - All 104 scene objects match local object model directories.
    - OmniGibson recognizes `Merom_0_int`.
13. Smoke-tested actual Merom scene loading:
    - Scene import reached `Imported scene 0`.
    - Live server started on `127.0.0.1:8090`.
    - `/health`, `/state`, and `/video.mjpg` responded.
    - MJPEG stream produced JPEG frames.
14. Added `--scene-model` to the live runner so the app can load `Merom_0_int` without changing presets.
15. Implemented runtime ceiling visibility control for the top-down overview camera:
    - caches 8 Merom ceiling/roof-related prims
    - hides them in overview/viewer mode
    - restores them for robot/resident/non-overview views
    - supports `--preserve-ceiling` for original-visibility replay validation
16. Added `--cpu-dynamics` and confirmed the full `Merom_0_int` scene can run with the browser server on port `8080`.
17. Verified the live server while the full scene was running:
    - `/health` returned `{"status":"ok","mode":"omnigibson-live"}`
    - `/state` returned live resident, sensor, robot context, camera, and event data
    - `/video.mjpg` returned JPEG MJPEG frames
    - movement command changed resident position from `[0.0, -1.2, 0.0]` to `[0.4, -1.2, 0.0]`
    - robot context `resident_position` updated with the movement
    - robot camera source and resident/overview camera presets switched successfully
18. Refined the browser UI into a full-screen monitoring console:
    - live camera fills the window
    - camera selection moved to a top-right dropdown
    - task selection moved to a bottom-right dropdown/run control
    - keyboard hint is shown as a translucent bottom-left overlay
    - event log panel was removed from the client
19. Added server-side event logging:
    - important events print to the server terminal
    - events are appended to `BEHAVIOR-1K/logs/live_scene_events.jsonl`
20. Improved resident control:
    - browser movement now sends smaller continuous WASD/arrow-key deltas
    - simulator movement is smoothed toward a target position
    - resident heading rotates toward movement direction
    - scene object bounding boxes are cached as collision blockers
    - initial resident spawn is adjusted to the nearest free position if the preferred coordinate overlaps furniture
21. Re-tested the upgraded live server:
    - full `Merom_0_int` scene loaded with `--cpu-dynamics`
    - server started on port `8080`
    - scene bounds cached: center approximately `[1.25, 4.15, 0.0]`
    - collision obstacles cached: 61
    - initial resident position adjusted from `[0.0, -1.2, 0.0]` to `[0.54, -0.48, 0.0]`
    - movement command changed resident position from `[0.54, -0.48, 0.0]` to `[0.585, -0.48, 0.0]`
22. Refactored the live demo code into `smart_home/live/`:
    - `runner.py`: OmniGibson lifecycle, command queue, sensors, camera, and simulator loop
    - `server.py`: FastAPI app, MJPEG stream, WebSocket broadcast, and command endpoints
    - `avatar.py`: procedural resident avatar construction
    - `constants.py`: camera, ceiling, movement, and collision tuning constants
    - `media.py`: RGB-to-JPEG encoding and action-space helpers
    - `static/`: monitoring UI assets
    - old `examples/.../run_live_control_scene.py` and `services/control_server/live_scene_app.py` remain as thin compatibility entry points
23. Verified the refactored code:
    - Python compile passed for live runner/server/avatar/constants/media and compatibility entry points
    - `smart_home.live.server` imports successfully and serves static assets from `smart_home/live/static`
    - full `Merom_0_int` scene loaded with `--cpu-dynamics`
    - `/health`, `/state`, `/`, and `/video.mjpg` responded
    - movement command updated resident position from `[0.54, -0.48, 0.0]` to `[0.585, -0.48, 0.0]`
24. Added the first Electron desktop client under `clients/homesense-electron/`:
    - packaged as a separate client app from the OmniGibson server
    - connects to a user-configured DGX/server URL
    - renders `/video.mjpg`
    - receives live state over `/ws`
    - sends HTTP commands for resident movement, camera switching, reset, and task requests
    - provides a monitoring-style full-screen UI with camera dropdown, task controls, keyboard hint, and compact telemetry
25. Installed Electron client dependencies:
    - Electron version: `v33.4.11`
    - `npm run check` passes
    - production dependency audit with `npm audit --omit=dev` reports `0 vulnerabilities`
26. Verified the Electron client against the live `Merom_0_int` server:
    - live server started on `0.0.0.0:8080`
    - Electron app connected to `/health`, `/state`, `/video.mjpg`, and `/ws`
    - keyboard movement requests reached the server and updated resident target position
    - camera preset changes reached the server
27. Fixed robot context zone freshness:
    - `robot.context.resident_zone` now mirrors the current `human.zone`
    - previous sensor-detected zone is preserved separately as `robot.context.last_known_resident_zone`
    - verified after moving the resident outside the motion sensor area: `human.zone == "unknown"`, `active_motion_sensor == null`, and `robot.context.resident_zone == "unknown"`
28. Rotated the top overview camera view 90 degrees counterclockwise:
    - overview camera now uses a custom top-down orientation so the long Merom floor plan is horizontal in the client
    - resident arrow-key mapping is screen-relative in overview mode
    - keyboard hint now shows arrow keys in a stable inverted-T layout instead of WASD
    - browser fallback UI and Electron UI were kept in sync
29. Removed unclear fixed camera presets from the client:
    - removed `Living Room` and `Laundry` camera options because they were old fixed-coordinate test views and not reliable Merom room views
    - retained `Top Overview`, `Robot Follow`, `Resident Follow`, and `Robot Camera`
30. Reworked the smart-home motion sensor rig for a more natural Merom demo layout:
    - `SmartHomeSensorRig` now supports multiple motion sensors
    - installed seven Merom motion sensors across entry/living, lower hall, center hall, bedroom, bathroom, kitchen/dining, and upper room zones
    - sensor visuals were reduced to smaller wall-mounted PIR-like markers with faint FOV overlays
    - active sensor selection now uses the nearest detected sensor
    - `human.zone`, `motion.sensor_id`, `motion.active_sensor_id`, and `robot.context.active_motion_sensor` now reflect the active multi-sensor result
    - verified `motion_entry_living` at spawn and `motion_lower_hall` after moving the resident along the hall
31. Added resident visual/collision separation for later replay validation:
    - the procedural resident visual remains under `/World/dummy_human`
    - a simplified capsule collision proxy is created under `/World/dummy_human/collision_proxy`
    - default `--human-collision-mode solid` keeps the resident as a physical obstacle for demo interaction
    - `--human-collision-mode ghost` allows replay validation without resident obstruction when needed
    - `--show-human-collision-proxy` can expose the proxy for debugging
    - `/state` now reports the current `human_collision` mode and proxy visibility
    - verified Python compile, Electron syntax check, and live `/state` reporting in solid mode
32. Replaced the visible resident avatar with a real local humanoid USD asset:
    - resident visual currently uses `asset_pipeline/b1k_pipeline/tools/HumanFemale/HumanFemale.usd`
    - old primitive resident model remains only as a fallback if the USD file is missing
    - movement and heading still apply to `/World/dummy_human`
    - hidden capsule collision proxy remains separate from the visible human asset
    - verified live server startup log reports the selected human USD asset
    - relaunched Electron against the updated live server
    - attempted `HumanFemale.keepAlive.usd` for a better idle pose, but reverted because it triggered an OmniGibson/PhysX/Fabric crash in this environment
33. Removed conflicting laundry/pressure demo visuals from the Merom scene:
    - stopped spawning the demo laundry basket and animated heavy laundry load
    - disabled the visible pressure pad and pressure gauge geometry
    - kept pressure sensor state in the API as an internal zero-weight reading
    - verified live server startup log reports `pressure_sensor_visual_disabled`
    - relaunched Electron against the updated live server
34. Switched the Merom demo/replay plan to a doorless scene variant:
    - collaborator replay collection and our demo will both use the same room-door-removed environment
    - all interior room doors are now treated as intentionally absent, not as temporarily opened runtime objects
    - generated `BEHAVIOR-1K/datasets/behavior-1k-assets/scenes/Merom_0_int/json/Merom_0_int_doorless.json`
    - preserved the original `Merom_0_int_best.json`
    - removed these door objects from the doorless JSON:
      `door_lvgliq_0`, `door_lvgliq_1`, `door_lvgliq_2`, `door_lvgliq_3`, `door_lvgliq_4`, `door_ohagsq_0`
    - live runner now loads the generated doorless scene file by default for `Merom_0_int`
    - live runner disables flatcache by default for the doorless Merom variant to avoid PhysX/Fabric flush instability
    - deprecated runtime door hiding because editing door prim visibility after scene load caused a PhysX/Fabric crash
    - verified the doorless JSON parses and has 98 init/state objects with no remaining door keys
    - verified live doorless Merom startup on `0.0.0.0:8080`
    - `/health` returned `{"status":"ok","mode":"omnigibson-live"}`
    - `/state` confirmed `doorless_scene_file_selected`, `pressure_sensor_visual_disabled`, `HumanFemale.usd`, and live video availability
    - `/command/move-human-delta` moved the resident from approximately `[0.54, -0.48, 0.0]` to `[0.585, -0.48, 0.0]`, and sensor/robot context updated
    - verified Python compile for `smart_home/live/runner.py`, `smart_home/live/avatar.py`, and `smart_home/sensors.py`
    - later corrected this plan to preserve the front entrance door candidate `door_ohagsq_0`
    - generated `BEHAVIOR-1K/datasets/behavior-1k-assets/scenes/Merom_0_int/json/Merom_0_int_no_interior_doors.json`
    - the current replay/demo scene now removes only the five interior `door_lvgliq_*` objects and preserves `door_ohagsq_0`
    - live runner now selects `Merom_0_int_no_interior_doors.json` by default for `Merom_0_int`
    - verified live startup on `127.0.0.1:8092`; `/health`, `/state`, and `/video.mjpg` responded
35. Updated the motion sensor layout to follow the current `센서 정보.md` draft:
    - confirmed the document currently specifies four concrete sensors:
      entrance/living door-side wall, TV table toward sofa, coffee-table ceiling, and bathroom left wall toward toilet
    - replaced the older seven-sensor test layout with six sensors:
      `motion_entry_door_wall`, `motion_living_tv_console`, `motion_living_coffee_ceiling`,
      `motion_bath_left_wall`, `motion_bedroom_entry_wall`, and `motion_inner_laundry_piano`
    - the bedroom and inner laundry/piano sensors are provisional because those sections of `센서 정보.md` are still incomplete
    - updated `smart_home/configs/merom_0_int_sensors.yaml` to mirror the runtime layout
36. Added runtime motion sensor range visualization:
    - motion sensor FOV/range meshes are created but hidden by default
    - Electron and browser fallback clients now expose a `Sensor ranges` toggle
    - toggling on shows semi-transparent sensor coverage in the simulator scene
    - toggling off hides the coverage again
    - fixed an initial USD edit crash by applying visibility changes inside `og.sim.editing_usd()`
    - verified `/state` reports `sensor_visualization.motion_ranges_visible`
    - verified both on and off commands against the live Merom server
37. Prepared a collaborator replay-environment export package:
    - created `collab_env_export_2026-05-24/` at the project root
    - created `collab_env_export_2026-05-24.tar.zst`
    - included the `Merom_0_int_no_interior_doors.json` scene, Merom object subset, BEHAVIOR metadata/systems, and `r1`/`r1pro` robot asset subsets
    - excluded sensor placement, monitoring UI, live server code, logs, and other demo-only files
    - added `README.md`, `MANIFEST.txt`, `SHA256SUMS.txt`, and `scripts/validate_export.py`
    - verified the export with `python scripts/validate_export.py`
    - archive size: approximately 970 MB compressed, 1.5 GB unpacked
    - archive SHA256: `0a317906392428b4c2a80efd83246b07668bdb6aea723238e186c9af37838105`
    - also created a Windows-friendly zip archive: `collab_env_export_2026-05-24.zip`
    - zip archive size: approximately 1.1 GB
    - zip archive SHA256: `55e5e2185abf6b7e77be09982025dd4e692b6bddfedfd2b9d53c3ed0028e6fc6`
    - verified the zip archive with `zip -T`

## Important Decisions

### Scene Asset Strategy

- Do not run `setup.sh --dataset` for this project unless explicitly approved.
- The full BEHAVIOR dataset/demo data is too large for our current needs.
- Use a focused asset subset for `Merom_0_int`.
- Keep the imported subset under:

```text
BEHAVIOR-1K/datasets/behavior-1k-assets/
```

### Robot Asset Strategy

- Use OmniGibson `R1Pro` as the current placeholder because the final Galaxea R1 asset/replay package is not yet available.
- When the collaborator delivers Galaxea R1 replay data, validate whether it can run against the same scene and whether the robot asset paths/model identifiers need remapping.

### Viewport / Browser Streaming Strategy

- Do not depend on Omniverse real-time streaming/WebRTC.
- Use simulator camera frames captured in OmniGibson and serve them through MJPEG.
- Browser/Electron video source selection should switch between:
  - overview / scene camera
  - robot camera
  - future resident camera or resident-follow view

### Desktop Client Strategy

- Keep simulation, BEHAVIOR assets, and FastAPI/OmniGibson runtime on the DGX/server machine.
- Use Electron as a cross-platform client shell for Windows/macOS/Linux laptops.
- Client laptops connect to the server URL over the network and do not need the BEHAVIOR dataset.
- For macOS distribution, a proper Mac build/signing/notarization path still needs to be prepared. A Linux-built DMG is not the final answer for Mac distribution.

### Roof / Ceiling Visibility Strategy

The top-down view needs the house interior to be visible. The selected approach is:

- Do not permanently edit the dataset scene files.
- Do not delete roof/ceiling objects.
- At runtime, cache ceiling/roof prims.
- When top-down scene camera is selected, hide ceiling/roof prims by setting visibility.
- When robot camera or resident camera is selected, restore ceiling/roof visibility.
- This preserves object paths and scene structure, reducing replay compatibility risk.

Reasoning:

- Permanently modifying scene JSON/USD can break replay compatibility.
- Runtime visibility changes are much lower risk because prim paths and physics/object state remain present.
- If replay validation requires original visibility, add a flag such as `--preserve-ceiling`.

### Replay Compatibility Policy

- Avoid deleting scene objects.
- Avoid changing scene object names, paths, or initial object poses.
- Prefer runtime-only visual changes.
- Keep a path to run the scene with original visibility when validating collaborator replay data.

Exception:

- Interior room doors are now an explicit project-level exception.
- The collaborator and this demo will use the same `Merom_0_int_no_interior_doors.json` scene variant.
- Replay compatibility should be validated against that no-interior-doors file, not against the original `Merom_0_int_best.json`.
- If original-scene validation is needed, launch with `--no-doorless-scene`.

## Known Risks

- Imported asset `VERSION` is `3.7.2rc1`, while current main branch code expects newer asset constants such as `3.9.0rc7`.
- Smoke test succeeded despite the version gap, but future object/state features could still expose incompatibilities.
- Current sensor/laundry/robot context coordinates still need Merom-specific tuning.
- Current motion sensor positions are a first Merom-specific pass based on `센서 정보.md`. Four sensors are document-backed; bedroom and inner laundry/piano sensors are provisional until the document is completed and visually validated.
- Resident collision now uses top-level scene-object bounding boxes. It is useful for demo blocking but less precise than a true navigation mesh.
- The resident now uses a real local humanoid USD asset, but it is still not animated and currently moves in a static pose.
- Resident collision is currently a simplified hidden capsule proxy. It is appropriate for demo blocking, but real robot/replay collision behavior must be validated once collaborator replay data is available.
- Robot replay integration is still placeholder-based until collaborator replay data is delivered.
- Full-scene execution with GPU dynamics produced a PhysX CUDA error 700 and crashed in this environment.
- Current full-scene demo should be launched with `--cpu-dynamics`.
- The Electron app currently has no authentication or TLS. It is suitable for local demo networks, not an exposed network.
- Linux Electron startup disables Chromium's sandbox because the current DGX environment blocks Electron's default sandbox setup.
- macOS packaging/signing/notarization is not configured yet.
- Runtime door hiding is unsafe in this environment. It caused a PhysX/Fabric crash, so door removal must happen through the scene JSON selected before load.
- `HumanFemale.keepAlive.usd` is currently unsafe in this environment. It caused a PhysX/Fabric crash after startup, so the resident visual is pinned to the stable `HumanFemale.usd` until we implement a safer pose/animation path.
- The visible resident may still appear in a static/T-pose-like stance. Fixing this should be done by validating an animation/pose approach in isolation, not by swapping to `HumanFemale.keepAlive.usd` directly.

## How To Run Current Merom Demo

From the BEHAVIOR-1K directory:

```bash
cd /home/user/Projects/csp-2026-k/BEHAVIOR-1K
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/run_live_control_scene.py \
  --scene-model Merom_0_int \
  --full \
  --cpu-dynamics \
  --robot-type R1Pro \
  --host 0.0.0.0 \
  --port 8080 \
  --video-fps 8
```

For `Merom_0_int`, this loads `Merom_0_int_no_interior_doors.json` by default. To compare against the original scene, add:

```bash
--no-doorless-scene
```

Then open:

```text
http://127.0.0.1:8080/
```

or use the machine's LAN IP from another client on the same network.

Last successful local LAN candidates observed:

```text
http://10.32.253.88:8080/
http://10.48.63.249:8080/
```

## How To Run Current Electron Client

From the Electron client directory:

```bash
cd /home/user/Projects/csp-2026-k/BEHAVIOR-1K/clients/homesense-electron
npm install
npm run start
```

To point the app at a remote DGX/server machine:

```bash
HOMESENSE_SERVER_URL=http://SERVER_LAN_IP:8080 npm run start
```

The app can also change the server URL from the connection panel.

## Immediate Next Steps

1. Visually tune the Electron UI with the user.
   - Confirm the top overview framing after automatic scene-bounds centering.
   - Adjust overlay sizes if they cover important scene content.
   - Check MacBook screen sizes and trackpad/keyboard feel.

2. Visually verify the doorless Merom scene in Electron.
   - Server-side live startup and movement API are verified.
   - User still needs to visually confirm all interior room doors are absent in the client view.
   - Confirm the top overview camera still frames the whole home.
   - Confirm resident movement no longer blocks at removed doorways.

3. Visually tune Merom resident/sensor/demo object placement.
   - Toggle `Sensor ranges` on in the Electron app and confirm each coverage area is physically plausible.
   - Confirm the four document-backed sensors match the intended real smart-home placement.
   - Complete the `센서 정보.md` bedroom and inner laundry/piano sections, then replace the provisional sensors.
   - Keep the pressure sensor internal/hidden for now because the previous visible pad overlapped laundry geometry.
   - Revisit robot starting context after replay files arrive.

4. Upgrade resident navigation fidelity.
   - Current movement uses bbox collision blocking.
   - Next step is a true floor-plan/navmesh style allowed-walkable-region model.

5. Improve resident animation.
   - Visible resident currently uses the stable `HumanFemale.usd`.
   - Do not use `HumanFemale.keepAlive.usd` directly; first isolate why it crashes.
   - Next step is adding a safe idle/standing pose and then walking animation or at least pose cycling while movement input is active.

6. Re-test live Electron controls against the actual Merom scene from the user's machine.
   - Camera source switching: server-side API verified.
   - Camera presets: server-side API verified.
   - Keyboard movement: Electron-to-server path verified; manual feel tuning still needed.
   - Sensor readings: state updates verified, but Merom-specific sensor regions need tuning.
   - Robot context updates: verified.
   - Task-running movement lock: implemented; needs client-side manual validation during task execution.

7. Prepare replay integration hook.
   - Keep current placeholder replay selection.
   - Add a clear interface for collaborator-provided Galaxea R1 replay files.
   - Validate replay against `Merom_0_int_no_interior_doors.json`, because replay collection and demo now share the same no-interior-doors environment.

## Status Check Procedure

Use this file as the primary project status checkpoint. Update it after:

- scene asset changes
- simulator setup changes
- demo server/UI changes
- replay integration changes
- major validation runs
- important design decisions

Minimum status check commands:

```bash
cd /home/user/Projects/csp-2026-k/BEHAVIOR-1K
test -f datasets/omnigibson.key
test -d datasets/behavior-1k-assets/scenes/Merom_0_int
test -d datasets/behavior-1k-assets/objects
python - <<'PY'
import json
from pathlib import Path
scene = Path("datasets/behavior-1k-assets/scenes/Merom_0_int/json/Merom_0_int_no_interior_doors.json")
doors = {"door_lvgliq_0", "door_lvgliq_1", "door_lvgliq_2", "door_lvgliq_3", "door_lvgliq_4"}
front_door = "door_ohagsq_0"
data = json.loads(scene.read_text())
assert not (doors & set(data["objects_info"]["init_info"]))
assert not (doors & set(data["state"]["registry"]["object_registry"]))
assert front_door in data["objects_info"]["init_info"]
assert front_door in data["state"]["registry"]["object_registry"]
print("no-interior-doors scene ok")
PY
/home/user/Desktop/isaac-sim-5.1/python.sh -m py_compile \
  smart_home/live/runner.py \
  smart_home/live/server.py \
  smart_home/live/avatar.py \
  smart_home/live/constants.py \
  smart_home/live/media.py \
  examples/smart_home/run_live_control_scene.py \
  services/control_server/live_scene_app.py
```
