# Final Presentation Completion Draft

Source PDF: `2026-C&S Project_Final_Team-K.pdf`

This document lists slide-by-slide content that can be copied into the unfinished parts of the final presentation. It also notes wording that should be corrected to match the actual implementation status.

## Slide 2C. Project Plan Change & Update

Replace the placeholder line with the following third update item.

### #3: 완전 자동 학습 루프 구현 범위 조정

Planned:

- 디지털 트윈에서 생성한 로봇 실행 데이터를 이용해 자동으로 정책 모델을 fine-tuning
- 다양한 거주자 행동과 센서 입력을 기반으로 closed-loop robot learning pipeline 구성
- Task 성공/실패를 모델 학습에 직접 반영

Revised:

- 최종 프로토타입에서는 실제 policy 학습 대신, 학습 가능한 데이터셋 생성 파이프라인을 우선 구현
- Teleoperation replay 실행 중 로봇 action, robot state, camera frame, smart-home context, task quality label을 task-run 단위로 기록
- 성공/실패 자동 라벨링 및 HDF5 export를 통해 추후 LeRobot / VLA fine-tuning에 연결 가능한 구조로 확장

Reason:

- 한정된 기간 내 실제 household policy fine-tuning까지 완료하기에는 데이터 양과 GPU 학습 시간이 부족
- 대신 교수님 피드백을 반영하여 "좋은 품질의 데이터가 디지털 트윈에서 생성되는가"를 검증 대상으로 전환

## Slide 3A. Project Design Validation

Recommended wording updates:

- `Dummy Asset` 대신 `Resident proxy asset` 또는 `resident context asset` 사용
- `센서의 경우 공식 Asset이 없어 rule-based로 직접 구현`은 아래처럼 보완

Suggested replacement:

- 스마트홈 센서는 실제 물리 센서 에셋이 아니라, 디지털 트윈 상의 위치/방향/FOV/Line-of-Sight를 가진 virtual sensor로 구현
- 모션 센서, 압력/가전/환경 센서 이벤트를 resident scenario와 scene geometry 기반으로 생성
- 생성된 sensing context는 robot task execution dataset의 environment context로 기록

## Slide 3B. Project Design Validation

Add the actual data flow description:

- `N`: resident scenario 생성
  - activity context 선택
  - resident position / posture 설정
  - sensor layout 선택 또는 유지

- `T`: task replay 실행 및 task-run dataset 기록 시작
  - HDF5 teleoperation replay 로드
  - R1Pro action replay 수행
  - robot state / action / camera frames / sensor context 기록

- `R` 또는 replay 종료: task-run 기록 종료
  - sub-goal based quality label 계산
  - JSONL + HDF5 dataset export
  - success / partial / failure score 저장

## Slide 3C. Demo Scenario

Fill the bottom-right "시나리오 설명 및 환경 정보" area with:

- Scenario 1: 거주자가 거실 소파에 앉아 있는 상황에서 약 전달 task 수행
- Scenario 2: 거주자가 침대에 누워 있는 상황에서 약 전달 task 수행
- Scenario 3: 거주자가 화장실에 있는 상황에서 실패 replay 및 자동 실패 라벨 확인
- 각 시나리오에서 resident context, active sensors, robot action, camera frame, task quality score가 task-run 단위로 저장됨

## Slide 4A. Implementation

Current slide has duplicated numbering: `1, 3, 3`. Use this corrected structure.

### 1. Digital Twin Runtime

- OmniGibson / Isaac Sim 기반 `Merom_0_int` smart-home scene 로딩
- Teleoperation 수집 씬과 동일한 door-modified scene variant 사용
- Galaxea R1Pro robot asset, medicine bottle object, resident proxy asset 배치
- Omniverse viewport 내에서 camera mode, scenario reset, replay start/pause/reset 조작

### 2. Smart-Home Sensing

- Motion sensor를 위치, 방향, FOV, 감지 거리 기반 virtual sensor로 구현
- Wall collision 기반 line-of-sight 검사로 벽 너머 감지 방지
- Dense / Current / Sparse sensor layout 전환 지원
- Resident activity context에 따라 pressure, appliance, ambient virtual sensor event 생성
- Sensor range visualization On/Off 지원

### 3. Resident Scenario Randomization

- `N` 입력으로 독립 episode context 생성
- 거실 소파 착석, 침대 눕기, 화장실 사용, 샤워, 피아노 연주, 입실 등 context별 resident pose 구현
- Context별 resident position / posture / expected active sensor를 metadata로 기록
- Replay 중에는 resident input을 제한하여 robot task execution dataset의 조건으로 고정

### 4. Replay-Based Robot Task Execution

- 협업자가 수집한 R1Pro teleoperation HDF5 replay 로딩
- Scenario context에 따라 적절한 replay 자동 선택
- `T` 입력 시 task replay 시작, `R/I` 입력 또는 replay 종료 시 task-run 종료
- Replay 중 robot state, action vector, camera frame, sensor context를 저장

### 5. Dataset Export & Auto Labeling

- Task-run 단위 dataset folder 생성
- `steps.jsonl`, `events.jsonl`, `metadata.json`, `manifest.json`, `annotations.json`, `quality_report.json` 저장
- Robot camera frame을 camera source별 JPEG로 저장
- 동일 내용을 `dataset.hdf5`로 export하여 학습 파이프라인 연계 가능
- grasp / transport / placement sub-goal 기반 task quality score 자동 계산

## Slide 4A. Verifying Implementation

Add details next to each visual block.

### 수집된 로봇 카메라 센서 프레임 데이터

- Replay 시작 시점부터 종료 또는 reset 시점까지 저장
- Camera source별 디렉토리 분리
- 학습용 visual observation 또는 demo review 자료로 활용 가능

### 수집된 로봇 state 정보

- Robot base pose, orientation, joint state, task status 기록
- Resident context와 sensor context가 같은 time step에 함께 저장됨
- 환경 조건과 robot execution 결과를 함께 분석 가능

### 로봇 action sequence

- HDF5 replay의 action vector를 step 단위로 기록
- Controller summary 및 normalization metadata 포함
- Teleoperation 기반 behavior cloning / imitation learning 데이터로 확장 가능

### Task 성공 여부 자동 라벨

- Grasp: 약병과 gripper contact / proximity 조건
- Transport: 약병의 일정 시간 이상 이동 여부
- Placement: 목표 영역 내 안정적으로 위치했는지 여부
- 최종 score = sub-goal weighted sum

## Slide 4B. Before & After

The current "After" text says "OmniGibson Extension 기반 Omniverse App". This is not technically exact. Replace it with:

### Before

- 별도 Electron/Web UI에서 camera switching, resident control, sensor monitor를 제공
- Web UI가 있어 시연은 편했지만, 실제 Omniverse / Isaac Sim 사용 여부와 digital twin 확장성이 잘 드러나지 않음
- 화면 스트리밍 및 입력 전달 구조 때문에 조작 지연과 복잡도가 증가
- Robot replay, sensor context, dataset logging이 하나의 학습 데이터 생성 루프로 명확히 보이지 않음

### After

- 별도 client UI를 제거하고 OmniGibson / Isaac Sim viewport 안에서 직접 조작
- `N/T/R/I/F/L/1/2/3` 등 keyboard control로 scenario, replay, reset, sensor layout, camera view 제어
- Resident scenario, sensor layout, robot replay, dataset logging을 하나의 digital twin runtime으로 통합
- Task-run 단위로 robot action, state, camera frame, smart-home context, quality label을 자동 저장
- "서비스 앱"보다 "디지털 트윈 기반 데이터 생성 및 검증 프레임워크"라는 프로젝트 목표가 명확해짐

Recommended one-line conclusion:

- Before: 모니터링 중심 wrapper
- After: 학습 데이터 생성 중심 digital twin runtime

## Slide 5. Project Review: Achieved Outcomes

Use the following table content.

### Outcome #1: Smart-Home Digital Twin Runtime

Planned:

- 스마트홈 scene에 로봇, 거주자, 센서를 배치하고 상호작용 가능한 demo 구현

Achieved:

- `Merom_0_int` 기반 smart-home scene 구축
- R1Pro robot, resident proxy, medicine bottle, virtual sensor 배치
- Omniverse viewport 내 scenario control, camera view, sensor visualization, replay control 구현

Percentage:

- 90%

Remaining:

- 실제 resident animation / high-fidelity human asset은 제한적으로 구현

### Outcome #2: Sensor-Aware Context Generation

Planned:

- 스마트홈 센서 데이터를 robot context로 변환

Achieved:

- Motion sensor FOV + line-of-sight 감지 구현
- Dense / Current / Sparse sensor layout 다양화
- Activity context 기반 pressure / appliance / ambient virtual sensor event 생성
- Active sensor와 resident context를 robot task dataset metadata로 저장

Percentage:

- 85%

Remaining:

- 실제 센서 노이즈 모델, 장기 human activity sequence는 추후 보완 필요

### Outcome #3: Replay-Based Robot Task Dataset Generation

Planned:

- Teleoperation data를 활용해 robot task execution 및 학습 데이터 생성

Achieved:

- HDF5 replay 기반 R1Pro task execution 구현
- Scenario별 replay 자동 선택
- Task-run 단위 action, state, camera frame, event, metadata 저장
- JSONL + HDF5 dataset export 구현

Percentage:

- 80%

Remaining:

- 실제 policy fine-tuning 및 closed-loop inference는 후속 작업

### Outcome #4: Auto Labeling for Task Quality

Planned:

- Task 성공 여부를 자동 평가

Achieved:

- Grasp / transport / placement sub-goal 기반 rule-based label 구현
- Replay 종료, reset, interruption 모두 quality report로 기록
- Success / partial success / failure 비교 가능

Percentage:

- 75%

Remaining:

- 더 정교한 contact 판단, semantic goal region 정의, multi-object task 확장 필요

## Slide 5. Ecosystem & Pointers

### X+AI Ecosystem

Shared Infra:

- GIST GPU / DGX-class compute environment
- Isaac Sim compatible Ubuntu workstation
- GitHub repository for source sharing

Common SW Platforms:

- NVIDIA Omniverse / Isaac Sim
- OmniGibson / BEHAVIOR-1K
- Python, PyTorch-compatible data pipeline
- HDF5 / JSONL dataset format

Data Sharing:

- HDF5 teleoperation replay
- Task-run dataset folders
- Robot camera frame dataset
- Metadata / quality report / annotations

### AI Model Software Pointers

Developed:

- HomeSense digital twin runtime
- Virtual smart-home sensing module
- Resident scenario randomizer
- Replay-to-dataset logging pipeline
- Rule-based task quality evaluator

Recycled:

- Isaac Sim runtime
- OmniGibson environment loading
- BEHAVIOR-1K scene / object assets
- R1Pro robot asset and teleoperation replay format

Integrated:

- Scenario context + sensor context + robot replay + camera observation
- Dataset export for future LeRobot / VLA fine-tuning
- Task quality score linked with robot execution data

### DataSets Pointers

Self-created:

- HomeSense task-run dataset
- Resident scenario metadata
- Sensor layout metadata
- Robot replay execution logs
- Camera frame observations
- Auto-labeled quality reports

Open / Public:

- BEHAVIOR-1K
- OmniGibson official scenes and objects
- Pretrained VLA model ecosystem as future target

Enhanced from Open:

- Door-modified `Merom_0_int` scene variant
- Medicine delivery task replay
- Smart-home sensor-aware context dataset built on top of BEHAVIOR-1K

## Slide 5. Files and Video

### Source Files

Software Folder / Files:

- `homesense-demo/`
- `BEHAVIOR-1K/smart_home/`
- `BEHAVIOR-1K/examples/smart_home/run_live_control_scene.py`
- `docs/`

Dataset Folder / Files:

- `replay-data/`
- `datasets/homesense_episodes/`
- `BEHAVIOR-1K/datasets/behavior-1k-assets/scenes/Merom_0_int/`
- `BEHAVIOR-1K/smart_home/configs/scenes/merom_0_int.yaml`

Demo Guide:

- `README.md`
- `docs/PROJECT_STATUS.md`
- `docs/EPISODE_DATA_SCHEMA.md`

### Demo Video

Software Snapshot:

- Omniverse / Isaac Sim viewport
- Top-down digital twin camera
- Sensor range visualization
- Scenario and replay keyboard controls

Dataset Snapshot:

- Task-run directory
- Camera frames
- `steps.jsonl`
- `metadata/dataset.hdf5`
- `quality_report.json`

Demo Video:

- Scenario 1: Living room delivery success
- Scenario 2: Bedroom delivery success
- Scenario 3: Bathroom failure / interrupted case
- Dataset logging and auto-labeling result

## General Corrections

- `Oniverse` -> `Omniverse`
- `OmniGibson Extension 기반 Omniverse App` -> `OmniGibson / Isaac Sim 기반 Omniverse viewport 통합 runtime`
- `DGX Spark` 표현은 실제 사용 장비가 명확하지 않으면 `DGX-class / GPU workstation` 또는 `GPU compute environment`로 완화
- `완전한 fine-tuning 완료`처럼 보이는 문장은 피하고, `fine-tuning 가능한 dataset generation pipeline 구현`으로 표현
- `Dummy Asset`은 부정적으로 보일 수 있으므로 `resident proxy asset`으로 교체
- `Cosmos 생태계의 확장성이 제한`은 너무 단정적이므로 `프로젝트 기간 내 Cosmos synthetic data pipeline 직접 연동은 범위 초과`로 수정

## Suggested Final Message

본 프로젝트는 완성된 상용 서비스 앱보다, 스마트홈 로봇 서비스를 위한 digital twin 기반 데이터 생성 및 검증 프레임워크에 초점을 맞추었다. OmniGibson / Isaac Sim 환경에서 resident scenario, virtual smart-home sensors, R1Pro teleoperation replay를 통합하고, task execution 과정에서 robot state, action, camera observation, sensor context, quality label을 task-run 단위로 자동 저장한다. 이를 통해 실제 로봇 정책 fine-tuning 전 단계에서, 특정 스마트홈 환경에 맞는 고품질 task dataset을 생성하고 검증할 수 있는 기반을 마련하였다.
