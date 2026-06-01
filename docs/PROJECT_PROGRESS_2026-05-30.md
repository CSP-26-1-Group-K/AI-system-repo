# HomeSense / OmniGibson Project Progress

Last updated: 2026-05-30

## 1. Current Direction

프로젝트 방향은 기존의 Electron/Web 기반 모니터링 앱 중심에서 **OmniGibson / Isaac Sim 기반 Digital Twin 데이터 생성 및 학습 루프 프로토타입**으로 전환했다.

현재 핵심 메시지:

```text
Digital Twin 기반 human-in-the-loop robot data generation & training loop prototype
```

중요한 정리:

- 디지털 트윈만으로 현실의 모든 인간 활동 패턴을 자동 생성하는 것은 목표가 아니다.
- 실제 가정용 로봇 서비스는 사람의 생활 패턴, 선호, 안전 판단이 중요하므로 human-in-the-loop가 필요하다.
- 현재 목표는 사람이 수집한 teleoperation / replay 데이터를 디지털 트윈에서 context variation과 함께 검증하고, 실패 조건과 추가 수집이 필요한 데이터를 찾는 기반을 만드는 것이다.
- 센서는 AI 모델 자체가 아니라 로봇 정책/태스크 선택에 들어가는 context source로 둔다.

## 2. Current Demo Surface

기본 시연 표면은 Electron 앱이 아니라 **OmniGibson / Isaac Sim viewport**다.

현재 기본 실행은 FastAPI/Electron gateway를 띄우지 않는다. 필요할 때만 `--serve-client`로 기존 Web/Electron 경로를 활성화한다.

주요 viewport 조작:

```text
1: Top overview camera
2: Resident follow camera
3: Robot follow camera
C: Cycle camera mode
W/A/S/D or arrow keys: Move resident
Q/E: Rotate resident in resident follow
F: Toggle motion sensor ranges
N: Start a randomized data-collection episode
T/Y: Trigger deliver-item / laundry replay placeholder
R: Reset scene
Esc: Quit
```

## 3. Current Scene / Robot

- Scene: `Merom_0_int`
- Scene variant: `Merom_0_int_no_interior_doors.json`
  - interior doors removed
  - front entrance door preserved
- Current robot placeholder: `R1Pro`
- Future robot/replay target: collaborator-provided Galaxea R1 replay data

Scene-specific data is now separated into:

```text
smart_home/configs/scenes/merom_0_int.yaml
```

This profile contains:

- resident start position
- semantic zones
- resident spawn anchors
- motion sensor layout
- pressure sensor metadata
- optional virtual activity sensor profiles
- doorless scene file path
- doorless portal clearance data
- ceiling hide target ids

## 4. Implemented Recently

### 4.1 Scene Profile Layer

Added:

```text
smart_home/scene_profile.py
smart_home/configs/scenes/merom_0_int.yaml
smart_home/configs/scenes/README.md
```

The runner now loads scene-specific configuration instead of directly hardcoding all `Merom_0_int` values.

### 4.2 Viewport Input Fix

Resident movement no longer queues accumulated movement commands. Input is now treated as a short-lived current input state, reducing the old inertia-like behavior when changing direction.

### 4.3 Episode Generator / Logger

Added:

```text
smart_home/episode_logging.py
```

The `N` key starts a new data-collection episode.

Each episode records JSONL events under:

```text
BEHAVIOR-1K/logs/homesense_episodes/
```

Recorded event types currently include:

- `episode_start`
- `episode_end`
- `task_started`
- `task_blocked`
- `task_completed`

Each record includes:

- episode id
- seed
- scene model
- sampled resident zone
- resident position
- motion sensor observation
- pressure state
- robot task/status/context
- minimum robot-resident distance metric

### 4.4 Resident Spawn Randomizer

The first version used `zone center + circular radius`, which could place the resident outside the house or across walls.

This was revised to use explicit safe `spawn_points` per zone with only a small jitter radius. This is more conservative and better for demo reliability.

Current validation requirement:

- Press `N` repeatedly in the viewport.
- Confirm the resident appears only in plausible indoor locations.
- Report bad zone/position cases so the corresponding spawn anchor can be removed or adjusted.

### 4.5 Task Placeholder Logging

`T` and `Y` currently do not execute real Galaxea replay.

Current behavior:

- `T`: tries `deliver_item` replay selection
- `Y`: tries `laundry` replay selection
- if no matching replay/context exists, the task is blocked and logged

This is expected until collaborator replay data is imported.

### 4.6 Optional Activity Sensor Layer

Added:

```text
smart_home/activity.py
```

The runner can now enable abstract smart-home activity sensors with:

```bash
--enable-activity-sensors
```

When enabled, each randomized episode samples a zone-specific activity profile from `smart_home/configs/scenes/merom_0_int.yaml`.

Current examples:

- `entry_living -> arriving_home`
- `living_room -> watching_tv / resting_on_sofa`
- `bathroom -> showering / bathroom_visit`
- `bedroom -> resting_in_bedroom / bedroom_activity`
- `utility_room -> doing_laundry / checking_laundry`

These profiles generate virtual sensor states such as floor mat occupancy, TV power, shower flow, humidity, bed/sofa pressure, washing machine state, and laundry weight.

The generated context is stored in:

- `activity_context`
- `robot.context.resident_context`
- `robot.context.virtual_sensors`

This is intentionally not presented as a trained AI model yet. It is a context-generation layer for richer episode data and later robot policy conditioning.

### 4.7 Step-Level Dataset Logging

Episode logging now produces both legacy event logs and an export-oriented run folder.

Legacy event log:

```text
BEHAVIOR-1K/logs/homesense_episodes/episodes_<timestamp>.jsonl
```

Dataset folder:

```text
BEHAVIOR-1K/logs/homesense_episodes/run_<timestamp>/
  manifest.json
  events.jsonl
  steps.jsonl
```

`events.jsonl` stores sparse lifecycle events such as `episode_start`, `episode_end`, `task_started`, and `task_blocked`.

`steps.jsonl` stores compact time-series records at `--step-log-hz` frequency. Default is 2 Hz. It includes:

- episode id / seed / sampled zone
- frame and simulator time
- resident state
- motion sensor state
- pressure state
- robot state/context
- activity context
- episode metrics

### 4.8 Omni Viewport HUD

An Omni UI window named `HomeSense Live Context` is shown in the simulator by default.

It summarizes:

- current episode id and sampled zone
- resident zone and position
- active motion sensor
- sampled activity id
- virtual sensor summary
- robot task/status
- current dataset export directory

It can be disabled with:

```bash
--disable-viewport-hud
```

### 4.9 Replay Import Validator

Added:

```text
smart_home/replay_import.py
examples/smart_home/validate_replay_import.py
smart_home/configs/replay_import_manifest.example.json
```

This does not execute replay yet. It validates collaborator-provided replay metadata before integration.

Example:

```bash
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/validate_replay_import.py \
  path/to/replay_import_manifest.json
```

If replay files are not copied yet, shape-only validation is available:

```bash
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/validate_replay_import.py \
  smart_home/configs/replay_import_manifest.example.json \
  --skip-path-check
```

### 4.10 Context Baseline

Added:

```text
smart_home/context_baseline.py
examples/smart_home/train_context_baseline.py
```

This is a small baseline, not a robot-control model. It reads generated `events.jsonl` or `steps.jsonl`, learns simple activity-to-zone and activity-to-task-hint mappings, and writes a JSON model.

Example:

```bash
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/train_context_baseline.py \
  logs/homesense_episodes/run_<timestamp>/events.jsonl \
  --output logs/homesense_episodes/context_baseline_model.json \
  --predict-activity showering
```

## 5. Current Limitations

1. **No real replay execution yet**
   - Galaxea R1 replay import is still pending.
   - Current task keys only test replay-selection plumbing and logging.

2. **No policy training yet**
   - Current work produces a dataset/logging structure.
   - It does not yet train or fine-tune a robot-control policy.
   - A simple context baseline exists only to demonstrate the data-to-model path.

3. **Sensor data is still partial**
   - Motion sensors can miss the resident.
   - This is realistic as partial observability, but task execution cannot rely on motion sensors alone.

4. **Activity sensors are abstract**
   - Floor mats, appliance states, and environmental sensors are represented as virtual sensor values.
   - Visual appliance animation is not implemented yet.

5. **Resident behavior is still simplified**
   - The resident can be moved manually or randomized at episode start.
   - Natural human activity trajectories are not yet modeled.

6. **Scene generalization is structural only**
   - Code now supports scene profiles.
   - Actual robot/replay validation remains focused on `Merom_0_int`.

## 6. Planned Next Work

### Step 1. Validate Activity Sensor Episodes

Run the viewport demo with `--enable-activity-sensors`, press `N` repeatedly, and verify that sampled activities match plausible resident locations.

Check JSONL logs under:

```text
BEHAVIOR-1K/logs/homesense_episodes/
```

Validation focus:

- activity profile matches sampled zone
- robot context includes virtual sensor evidence
- utility-room laundry activity can set a pressure-like laundry weight
- missing motion detection is still recoverable through activity context

### Step 2. Validate Step-Level Logging

Check:

```text
logs/homesense_episodes/run_<timestamp>/manifest.json
logs/homesense_episodes/run_<timestamp>/events.jsonl
logs/homesense_episodes/run_<timestamp>/steps.jsonl
```

Confirm that `steps.jsonl` grows while the simulator is running.

### Step 3. Refine Resident Context Estimator

Raw sensors should be converted into a context estimate:

```text
resident_zone_estimate
confidence
evidence
last_seen_zone
last_seen_age_s
```

The current implementation stores `zone_estimate`, `confidence`, and `evidence`; last-seen aging is still pending.

This separates:

- simulator ground truth
- smart-home sensor observation
- estimated resident context

### Step 4. Improve Task Gating

Task execution should eventually depend on context confidence:

- high confidence resident zone: task can proceed
- unknown/low confidence: human-dependent task should block or request confirmation
- resident-independent task: can proceed with safety layer

### Step 5. Import Real Replay Data

When collaborator data arrives:

- inspect replay format
- map Galaxea R1 replay to current scene/robot setup
- log observation/action/context during replay
- use randomizer to find context conditions where replay fails
- collect correction teleoperation for those failure cases

### Step 6. Stabilize Dataset Schema

The episode JSONL schema should become stable enough to support:

- behavior cloning dataset export
- replay selection baseline
- failure case analysis
- presentation examples

## 7. What The User Should Validate

Immediate validation tasks:

1. Press `N` repeatedly and check resident spawn positions.
   - If the resident appears outside the house, inside a wall, or in an implausible place, record the zone and screenshot.

2. Press `F` to show sensor ranges.
   - Check whether sensor misses are plausible or caused by bad sensor placement.

3. Press `T` / `Y`.
   - Expect many `task_blocked` cases for now.
   - This is normal until real replay data and richer context sensors are connected.

4. Inspect JSONL logs only when needed:

```bash
tail -n 20 /home/user/Projects/csp-2026-k/BEHAVIOR-1K/logs/homesense_episodes/<latest>.jsonl
```

5. Decide which activity profiles are important for the presentation:
   - showering
   - watching TV
   - doing laundry
   - arriving home
   - resting in bedroom

## 8. Current Recommended Demo Story

The recommended presentation story is:

1. Load the real OmniGibson `Merom_0_int` digital twin.
2. Show resident randomization by pressing `N`.
3. Show smart-home sensors and partial observability.
4. Explain that ground truth and sensor observation are both logged.
5. Trigger task placeholder and show that the system blocks when context/replay is insufficient.
6. Explain that this is exactly where human-in-the-loop correction and additional replay collection enter.
7. Show JSONL episode logs as generated training/evaluation data.

The point is not to claim completed autonomous robot learning. The point is to show a credible foundation for **context-rich data generation, failure discovery, and future replay/policy training**.

## 9. Dataset Quality Update

The generated dataset now separates simulator truth from inferred smart-home context.

Each step record includes:

- `ground_truth`: resident zone/pose, activity id, virtual sensor values, robot pose
- `estimates`: active sensor estimate, last-known zone, confidence, evidence
- `sensor_quality`: motion dropout, zone mismatch, and fault labels
- `risk`: robot-resident distance and risk level
- `training_validity`: which downstream uses are valid at the current project stage
- `scenario_type`: compact scenario label for dataset balancing

Important limitation:

- `policy_behavior_cloning` remains false until collaborator replay files provide action labels and controller metadata.
- Current logs are valid for context modeling, task selection experiments, safety evaluation, and presentation of the data-generation loop.

Dataset summary command:

```bash
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/summarize_episode_dataset.py \
  logs/homesense_episodes/run_<timestamp>
```

This produces counts and rates for zones, scenario types, activity labels, risk labels, motion detection, dropout, and training validity.
