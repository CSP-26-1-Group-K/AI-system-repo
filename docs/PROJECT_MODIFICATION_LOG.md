# 프로젝트 수정 계획 및 변경 내역

Last updated: 2026-05-30

## 1. 방향 전환 배경

기존 데모는 Electron 기반 모니터링 앱을 중심으로 구성되어 있었다. 이 방식은 서비스 앱처럼 보기에는 좋지만, 프로젝트의 핵심인 **Omniverse / Isaac Sim / OmniGibson 기반 Digital Twin 활용성**이 잘 드러나지 않는 문제가 있었다.

특히 외부에서 보면 실제 Omniverse 환경을 구동하는 것이 아니라, 미리 짜인 화면 위에서 웹 앱을 실행하는 것처럼 보일 수 있다. 또한 교수님이 기대한 방향은 완성된 서비스 UI보다 다음과 같은 **AI system loop**에 가깝다고 판단했다.

```text
Twin 상 행동 수행
  -> 센서 / 로봇 / 상태 / action 데이터 생성
  -> episode dataset 구성
  -> 학습 또는 policy update
  -> 다시 Twin에서 실행 및 검증
```

따라서 프로젝트의 주요 시연 방향을 **Electron/Web 모니터링 앱 중심**에서 **OmniGibson / Isaac Sim viewport 중심의 데이터 생성 및 학습 루프 시연**으로 수정한다.

## 2. 수정된 핵심 목표

기존 목표:

- 스마트홈 로봇 구독 서비스를 위한 별도 모니터링 클라이언트 구현
- Electron 앱에서 카메라 전환, 거주자 이동, 센서 상태 확인, Task 실행

수정 목표:

- 실제 OmniGibson / Isaac Sim viewport에서 Digital Twin이 동작하는 것을 직접 보여준다.
- 거주자 / 로봇 / replay / scripted behavior를 Twin 안에서 실행한다.
- 실행 중 생성되는 센서, scene state, robot state, action, context 데이터를 episode dataset으로 저장한다.
- 생성 데이터가 robot policy 또는 task model 학습에 어떻게 연결되는지 보여준다.
- 학습 또는 선택된 행동을 다시 Twin에서 검증하는 closed-loop 구조를 만든다.

## 3. AI 방향 재정의

센서 데이터 기반 zone prediction 모델을 추가하는 방안도 검토했지만, 현재 파이프라인에서는 핵심 AI로 두기 어렵다고 판단했다.

이유:

- 로봇 task 수행 중에는 로봇과 거주자 충돌을 막기 위해 거주자를 고정해야 할 가능성이 높다.
- 거주자가 고정되면 한 episode 내부의 센서 데이터 변화가 거의 없다.
- 센서 기반 zone prediction은 rule-based baseline보다 성능이 좋다는 보장이 없다.
- 잘못된 zone prediction은 오히려 robot task target이나 replay selection을 망칠 수 있다.

따라서 센서는 독립적인 AI prediction target이 아니라, **로봇 학습 데이터를 조건화하는 deterministic context generator**로 정의한다.

센서 데이터의 역할:

- active motion sensor
- resident zone
- last known resident zone
- resident fixed position
- pressure trigger
- task context metadata

이 정보는 robot observation-action dataset에 함께 저장되어, 향후 robot policy가 scene observation뿐 아니라 smart-home context까지 conditioning할 수 있게 하는 보조 입력으로 사용한다.

## 4. Electron/Web 클라이언트 처리 방침

Electron 및 browser client는 완전히 삭제하지 않고 **freeze된 auxiliary monitoring path**로 둔다.

현재 처리 방침:

- 기본 시연에서는 Electron/Web을 사용하지 않는다.
- 기본 실행에서는 FastAPI / Electron gateway를 띄우지 않는다.
- 필요할 때만 `--serve-client` 옵션으로 기존 gateway를 활성화한다.
- 기존 UI 코드는 보존하되, 프로젝트의 핵심 시연 표면으로 사용하지 않는다.

## 5. 이미 반영한 변경 내역

### 5.1 Viewport 중심 실행 모드 추가

`BEHAVIOR-1K/smart_home/live/runner.py`를 수정하여 기본 실행 시 FastAPI 서버를 시작하지 않도록 변경했다.

기본 실행:

```bash
cd /home/user/Projects/csp-2026-k/BEHAVIOR-1K
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/run_live_control_scene.py \
  --scene-model Merom_0_int \
  --full \
  --cpu-dynamics \
  --robot-type R1Pro
```

기존 Electron/Web client를 다시 쓰고 싶을 때:

```bash
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/run_live_control_scene.py \
  --scene-model Merom_0_int \
  --full \
  --cpu-dynamics \
  --robot-type R1Pro \
  --serve-client \
  --host 0.0.0.0 \
  --port 8080
```

### 5.2 Isaac / OmniGibson viewport 키보드 조작 추가

Electron 앱 없이도 Omniverse viewport에서 직접 조작할 수 있도록 키보드 callback을 추가했다.

현재 viewport controls:

```text
1: Top overview camera
2: Resident follow camera
3: Robot follow camera
C: Cycle camera mode
W/A/S/D or arrow keys: Move resident
Q/E: Rotate resident in resident follow mode
F: Toggle motion sensor ranges
T/Y: Trigger deliver-item / laundry replay placeholder
R: Reset scene
Esc: Quit
```

세부 동작:

- Top overview에서는 `W/A/S/D` 또는 방향키 입력이 화면 기준 이동으로 동작한다.
- Top overview 이동 시 거주자는 이동 방향을 바라본다.
- Resident follow에서는 `W/A/S/D`가 거주자 heading 기준 이동으로 동작한다.
- Resident follow에서는 `Q/E`로 heading을 회전한다.
- Robot follow에서는 거주자 이동 입력을 받지 않는다.
- 모든 조작은 simulator command queue를 통해 처리되어, 기존 서버 명령과 같은 실행 경로를 공유한다.

### 5.3 센서 범위 표시 토글 유지

`F` 키로 motion sensor range visualization을 켜고 끌 수 있게 했다.

기본값:

- 센서 범위는 숨김

표시 시:

- 반투명 FOV / range mesh를 scene 안에 표시

### 5.4 Task placeholder 단축키 유지

로봇 replay 데이터가 아직 완전히 통합되지 않았기 때문에 현재 task 실행은 placeholder 상태다.

단축키:

- `T`: deliver item placeholder
- `Y`: laundry placeholder

향후 협업자가 제공하는 Galaxea R1 replay 데이터와 연결할 예정이다.

### 5.5 GitHub 반영

공유 repo에도 변경 내용을 반영했다.

Repository:

```text
https://github.com/CSP-26-1-Group-K/AI-system-repo.git
```

Push된 commit:

```text
7cb9cfe Shift demo controls to OmniGibson viewport
```

## 6. 앞으로의 개발 계획

### Step 1. Viewport 조작 검증

- 실제 OmniGibson / Isaac Sim 화면에서 camera switching 확인
- `W/A/S/D`, 방향키 이동 확인
- resident follow에서 `Q/E` 회전 확인
- sensor range toggle 확인
- task placeholder 실행 로그 확인

### Step 2. Episode data logger 설계

Electron/Web 로그가 아니라, Twin 실행으로부터 직접 episode dataset을 생성하도록 logger를 재구성한다.

예상 저장 구조:

```text
data/episodes/
  episode_0001/
    metadata.json
    sensor_context.jsonl
    robot_state.jsonl
    scene_state.jsonl
    actions.jsonl
    replay_info.json
```

각 step에서 저장할 후보 데이터:

- timestamp
- task id
- scene id
- robot pose
- robot observation metadata
- robot action 또는 replay action
- resident position
- resident zone
- active motion sensor
- pressure state
- object state
- replay id

### Step 3. Replay / teleoperation data 연결

- 협업자가 수집한 Galaxea R1 replay 데이터를 import한다.
- 현재 R1Pro placeholder와 replay format 차이를 정리한다.
- replay 실행 중 sensor context와 robot state를 함께 logging한다.

### Step 4. 최소 AI 학습 루프 구성

단기적으로는 완전한 robot low-level policy보다 다음 중 하나를 우선 검토한다.

- task context -> replay selection
- scene/context -> navigation target selection
- observation/action replay dataset -> simple behavior cloning baseline
- sensor context가 포함된 robot action dataset 구성

핵심은 모델 성능보다 **Twin에서 생성된 데이터가 학습 입력이 되고, 학습 결과가 다시 Twin 실행에 영향을 주는 loop**를 보이는 것이다.

### Step 5. 발표 자료 수정

발표 자료의 표현을 다음 방향으로 수정한다.

기존 표현:

```text
스마트홈 로봇 구독 서비스 모니터링 앱 구현
```

수정 표현:

```text
Digital Twin 기반 robot data generation & training loop prototype
```

강조할 메시지:

- 실제 OmniGibson / Isaac Sim scene 사용
- 센서와 robot state가 Twin에서 직접 생성됨
- 생성 데이터가 episode dataset으로 저장됨
- dataset이 AI 학습 / policy update로 연결됨
- 학습 또는 선택된 행동을 다시 Twin에서 검증함

## 7. 남은 리스크

- 실제 Galaxea R1 replay import가 지연될 경우 robot learning loop가 약해질 수 있다.
- 현재 task 실행은 placeholder이므로, replay 연결 또는 scripted robot behavior가 필요하다.
- 센서 context는 deterministic rule 기반이므로, 이를 AI 모델이라고 주장하면 방어가 어렵다.
- 따라서 AI는 sensor prediction이 아니라 robot policy / task selection / behavior cloning 쪽으로 설명해야 한다.
- Viewport keyboard callback은 key release가 아니라 key press/repeat 기반이므로, game-like continuous control과는 조작감이 다를 수 있다.

## 8. 현재 결론

Electron 모니터링 앱은 보조 도구로 남기고, 프로젝트의 핵심 시연은 **Omniverse viewport에서 보이는 실제 Digital Twin + 데이터 생성 + 학습 루프**로 전환한다.

센서는 AI 자체가 아니라, robot learning episode를 조건화하는 context-producing infrastructure로 둔다. AI의 핵심은 로봇 행동 데이터 생성, 학습, 그리고 Twin 재검증 루프에 둔다.

## 9. Scene Profile 구조 추가

다양한 씬으로 확장할 수 있도록 실행 코드에서 `Merom_0_int` 전용 값을 직접 참조하던 부분을 `scene profile` 로딩 구조로 분리했다.

추가된 구조:

- `smart_home/scene_profile.py`
- `smart_home/configs/scenes/merom_0_int.yaml`
- `smart_home/configs/scenes/README.md`

현재 로봇 움직임 데모와 replay 검증 대상은 여전히 `Merom_0_int` 하나로 제한한다. 다만 새 씬을 추가할 때는 다음 데이터를 씬별 YAML로 정의하면 같은 runner 구조를 재사용할 수 있다.

- `scene_model`
- resident 초기 위치
- zone 정의
- motion / pressure sensor 배치
- sensor encoder zone order
- overview camera 설정
- doorless scene JSON 경로
- doorless portal collision clearance
- ceiling hide 대상

새 씬 확장은 `smart_home/configs/scenes/<scene_model_lowercase>.yaml` 파일을 추가하고 `--scene-model <SceneModel>`로 실행하는 방식으로 진행한다. 프로파일이 없는 씬은 기존 preset fallback으로 실행은 가능하지만, 센서-zone 매핑과 resident 초기화는 검증된 상태가 아니다.

## 10. Episode Generator / Logger 최소 구현

이틀 내 구현 가능한 현실적인 목표로, 완성형 로봇 학습이 아니라 **학습 가능한 rollout dataset을 생성하는 최소 루프**를 우선 구현한다.

구현된 기능:

- `N` 키로 새 data-collection episode 시작
- scene profile의 zone 안에서 resident 위치를 seed 기반 랜덤 샘플링
- 충돌 위치는 피하고, 실패 시 가장 가까운 free position으로 보정
- episode 시작 / task 시작 / task blocked / task 완료 이벤트를 JSONL로 저장
- episode record에 scene, seed, resident zone/position, motion sensor state, pressure state, robot context, 최소 robot-resident 거리 metric 포함
- `R` reset은 기본적으로 기존 시작 위치로 복귀하되, `--randomize-resident-on-reset` 옵션을 주면 reset도 randomized episode로 동작

실행 옵션:

```bash
--episode-seed 20260530
--resident-zone random
--episode-log-dir logs/homesense_episodes
--randomize-resident-on-reset
```

현재 이 기능의 의미:

- 아직 policy 학습 자체를 수행하지는 않는다.
- 대신 replay/scripted task/future policy rollout이 발생했을 때 학습에 필요한 context와 metric을 구조화해서 저장한다.
- 발표에서는 “완성된 AI 학습”이 아니라 “Digital Twin 기반 data generation loop의 첫 동작 단위”로 설명한다.

## 11. 문서 정리

프로젝트 문서 파일을 루트 디렉토리에서 `docs/` 폴더로 정리했다.

현재 문서 기준:

- `docs/PROJECT_PROGRESS_2026-05-30.md`: 최신 진행상황 및 향후 계획
- `docs/PROJECT_MODIFICATION_LOG.md`: 방향 전환 및 변경 이력
- `docs/PROJECT_STATUS.md`: 기존 상세 누적 기록
- `docs/README.md`: 문서 목록과 현재 기준 문서 안내

## 12. Optional Activity Sensor Layer 추가

motion sensor만으로는 거주자가 센서 사각지대에 있을 때 로봇 task context가 비어버리는 문제가 있다. 이를 보완하기 위해 실제 스마트홈에서 자연스럽게 존재할 수 있는 추상 센서 계층을 추가했다.

추가된 코드:

- `smart_home/activity.py`
- `smart_home/configs/scenes/merom_0_int.yaml`의 `activity_profiles`
- `smart_home/live/runner.py`의 `--enable-activity-sensors` 옵션

현재 구현은 물리 가전 애니메이션이 아니라 episode context generator다. `N`으로 새 episode를 시작하면 resident zone에 따라 activity profile을 샘플링하고, 다음과 같은 virtual sensor 값을 생성한다.

- entry floor mat / front door contact
- sofa pressure / TV power
- bathroom floor mat / shower flow / humidity
- bed pressure / bedroom light
- utility floor mat / washing machine state / laundry weight

생성된 값은 `activity_context`와 `robot.context.resident_context`에 기록된다. 이 데이터는 당장 AI prediction 모델로 주장하지 않고, 향후 robot policy 또는 replay selection을 조건화하는 context input으로 사용한다.

검증 방법:

```bash
cd /home/user/Projects/csp-2026-k/BEHAVIOR-1K
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/run_live_control_scene.py \
  --scene-model Merom_0_int \
  --full \
  --cpu-dynamics \
  --robot-type R1Pro \
  --resident-zone random \
  --randomize-resident-on-reset \
  --enable-activity-sensors
```

실행 후 viewport에서 `N`을 여러 번 누르고 `logs/homesense_episodes/`의 JSONL에 `activity_context`가 기록되는지 확인한다.

## 13. Step Logging / HUD / Replay Import / Baseline 추가

replay 데이터가 들어오기 전까지 구현 가능한 데이터 생성 루프 보강 항목을 추가했다.

추가된 기능:

- 실행 1회당 dataset export folder 생성
- `events.jsonl`과 `steps.jsonl` 분리
- `manifest.json` 저장
- Omni viewport 안의 `HomeSense Live Context` HUD 표시
- collaborator replay import manifest validator
- context baseline 학습 스크립트

생성 구조:

```text
BEHAVIOR-1K/logs/homesense_episodes/run_<timestamp>/
  manifest.json
  events.jsonl
  steps.jsonl
```

기본 step logging 주기는 2 Hz다.

실행 옵션:

```bash
--step-log-hz 2.0
--disable-viewport-hud
```

replay manifest 검증:

```bash
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/validate_replay_import.py \
  path/to/replay_import_manifest.json
```

context baseline 학습:

```bash
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/train_context_baseline.py \
  logs/homesense_episodes/run_<timestamp>/events.jsonl \
  --output logs/homesense_episodes/context_baseline_model.json
```

이 baseline은 로봇 제어 정책이 아니라, 생성된 context dataset이 실제 model artifact로 변환될 수 있음을 보여주는 최소 proof path다.
