# HomeSense Gym / Smart-Home Robot Digital Twin 제출 안내서

이 저장소는 Team K의 스마트홈 로봇 디지털 트윈 기반 데이터 생성 프로젝트입니다. 최종 프로토타입은 NVIDIA Isaac Sim / OmniGibson / BEHAVIOR-1K 환경 위에서 `Merom_0_int` 스마트홈 씬을 로딩하고, R1Pro 로봇의 teleoperation replay를 실행하면서 로봇 상태, action, 카메라 프레임, 스마트홈 센서 context, task 성공 라벨을 task-run 단위로 저장합니다.

본 README는 수업 조교님들이 제출 압축 파일을 Ubuntu + NVIDIA GPU 환경에서 검증할 수 있도록 작성한 한글 셋업 문서입니다.

## 1. 프로젝트 요약

### 1.1 목표

이 프로젝트의 목표는 완성된 상용 서비스 앱을 만드는 것이 아니라, 스마트홈 로봇 서비스를 위한 디지털 트윈 기반 학습 데이터 생성 환경을 실증하는 것입니다.

현재 구현은 다음 기능을 제공합니다.

- Isaac Sim / OmniGibson 기반 `Merom_0_int` 스마트홈 디지털 트윈 실행
- Galaxea R1Pro 로봇 asset 배치
- 약병 전달 task replay 실행
- 거주자 resident proxy asset 배치 및 시나리오 생성
- 스마트홈 virtual sensor 배치
- `sparse`, `current`, `dense` 센서 레이아웃 전환
- 거주자 시나리오별 sensor context 생성
- 로봇 replay 실행 중 state / action / camera frame 저장
- task 성공 여부 자동 라벨링
- JSONL 및 HDF5 데이터셋 export
- AI Gym Framework 확장을 위한 metadata / registry 구조 추가

### 1.2 최종 방향성

처음에는 특정 스마트홈 로봇 서비스 데모에 가까웠지만, 최종 제출 버전에서는 이를 `HomeSense Gym`이라는 AI Gym 형태의 첫 번째 environment로 일반화했습니다.

현재 등록된 첫 번째 environment는 다음 조합입니다.

```text
Environment: Merom_0_int v09 smart-home scene
Robot:      R1Pro
Task:       medicine_delivery
Policy:     teleoperation replay
Sensors:    virtual smart-home motion/activity sensors
Evaluator:  grasp / transport / place weighted score
Dataset:    homesense_task_run_v1
```

향후에는 다른 로봇, 다른 씬, 다른 task, 다른 sensor layout, 다른 evaluator를 registry 방식으로 추가할 수 있도록 설계했습니다.

## 2. 제출 압축 파일과 GitHub 압축 파일의 차이

GitHub에서 다운로드하는 zip 파일은 소스 코드와 문서 중심의 작은 snapshot입니다. `.gitignore`에 의해 대용량 실행 자원은 포함되지 않습니다.

반면 제출용 5GB대 압축 파일은 실행 검증을 위해 다음 항목을 포함하는 runtime snapshot입니다.

- `BEHAVIOR-1K/` 실행 디렉토리
- 수정된 HomeSense / OmniGibson 실행 코드
- 필요한 `Merom_0_int` scene subset
- 필요한 object asset subset
- 약병 custom asset
- replay HDF5 파일
- scene metadata JSON
- `homesense-demo/` 코드 mirror
- 문서와 발표자료

제출 압축 파일에는 Isaac Sim 본체는 포함하지 않았습니다. Isaac Sim은 설치 용량과 시스템 의존성이 크기 때문에 검증 PC에 별도로 설치되어 있어야 합니다.

## 3. 검증 PC 사전 조건

### 3.1 운영체제

권장 환경:

```text
Ubuntu Linux
NVIDIA GPU 탑재
NVIDIA GPU Driver 정상 설치
Isaac Sim 5.1 설치
```

이 프로젝트는 개발 PC에서 다음 경로 기준으로 실행되었습니다.

```text
/home/user/Projects/csp-2026-k
/home/user/Desktop/isaac-sim-5.1
```

검증 PC의 사용자 이름이나 설치 경로가 다르면 실행 명령의 경로만 바꿔주면 됩니다.

### 3.2 Isaac Sim

필수:

```text
Isaac Sim 5.1
```

검증 전 Isaac Sim Python 실행 파일이 존재하는지 확인합니다.

```bash
ls -lh /home/user/Desktop/isaac-sim-5.1/python.sh
```

만약 설치 경로가 다르다면 아래 예시의 `ISAAC_PYTHON` 값을 검증 PC 경로에 맞게 바꿔주세요.

예:

```bash
export ISAAC_PYTHON=/path/to/isaac-sim-5.1/python.sh
```

### 3.3 Conda 주의사항

Isaac Sim의 `python.sh`는 자체 Python 런타임을 사용합니다. Conda 환경이 켜져 있으면 다음 경고가 나올 수 있습니다.

```text
Warning: running in conda env, please deactivate before executing this script
```

가능하면 실행 전 Conda base 환경을 끄는 것을 권장합니다.

```bash
conda deactivate
```

경고가 나와도 실행되는 경우가 있지만, 문제가 발생하면 반드시 Conda를 끄고 다시 실행해 주세요.

## 4. 압축 해제

제출 압축 파일 이름이 예를 들어 `csp-2026-k_submission.zip`이라고 가정합니다.

권장 위치:

```text
/home/user/Projects/csp-2026-k
```

압축 해제 예시:

```bash
mkdir -p /home/user/Projects
cd /home/user/Projects
unzip /path/to/csp-2026-k_submission.zip -d csp-2026-k
```

만약 zip 안에 이미 `csp-2026-k/` 최상위 폴더가 포함되어 있다면, 다음처럼 중첩 폴더가 생길 수 있습니다.

```text
/home/user/Projects/csp-2026-k/csp-2026-k
```

이 경우 실제 프로젝트 루트로 이동해서 실행하면 됩니다.

```bash
cd /home/user/Projects/csp-2026-k
ls
```

정상적인 프로젝트 루트에는 다음 폴더와 파일이 보여야 합니다.

```text
BEHAVIOR-1K/
datasets/
docs/
homesense-demo/
replay-data/
README.md
```

## 5. 압축 해제 후 파일 구성 확인

프로젝트 루트에서 아래 명령을 실행합니다.

```bash
cd /home/user/Projects/csp-2026-k

test -d BEHAVIOR-1K
test -d BEHAVIOR-1K/OmniGibson
test -d BEHAVIOR-1K/smart_home
test -d BEHAVIOR-1K/datasets/behavior-1k-assets
test -d replay-data
test -f BEHAVIOR-1K/examples/smart_home/run_live_control_scene.py
test -f BEHAVIOR-1K/smart_home/configs/scenes/merom_0_int.yaml
test -f BEHAVIOR-1K/smart_home/configs/gym_registry.yaml
```

위 명령은 성공하면 아무 출력도 하지 않습니다. 실패하면 해당 파일이나 폴더가 없다는 뜻입니다.

주요 파일을 직접 확인하려면:

```bash
ls -lh replay-data
ls -lh BEHAVIOR-1K/smart_home/configs/scenes
ls -lh BEHAVIOR-1K/smart_home/configs/gym_registry.yaml
```

`replay-data/`에는 다음과 같은 HDF5 replay 파일들이 포함되어 있어야 합니다.

```text
delivery_failure_case.hdf5
delivery_med_room_1_v01_repaired_v09.hdf5
delivery_med_room_2_sc.hdf5
delivery_med_room_3.hdf5
```

## 6. 실행 전 경로 변수 설정

검증 PC에서 경로를 명확하게 하기 위해 아래 변수를 먼저 설정하는 것을 권장합니다.

```bash
export PROJECT_ROOT=/home/user/Projects/csp-2026-k
export ISAAC_PYTHON=/home/user/Desktop/isaac-sim-5.1/python.sh
```

만약 Isaac Sim 설치 위치가 다르면:

```bash
export ISAAC_PYTHON=/actual/path/to/isaac-sim-5.1/python.sh
```

확인:

```bash
test -f "$ISAAC_PYTHON"
test -d "$PROJECT_ROOT/BEHAVIOR-1K"
```

## 7. 기본 실행 명령

프로젝트 루트에서 아래 명령을 실행합니다.

```bash
cd "$PROJECT_ROOT/BEHAVIOR-1K"

TORCHDYNAMO_DISABLE=1 "$ISAAC_PYTHON" \
  examples/smart_home/run_live_control_scene.py \
  --scene-model Merom_0_int \
  --full \
  --cpu-dynamics \
  --robot-type R1Pro \
  --hdf5-replay-dir "$PROJECT_ROOT/replay-data" \
  --enable-activity-sensors \
  --sensor-layout current \
  --episode-log-dir "$PROJECT_ROOT/datasets/homesense_episodes" \
  --step-log-hz 2.0 \
  --save-camera-frames \
  --camera-log-fps 2.0 \
  --camera-log-sources robot \
  --camera-log-width 640 \
  --camera-log-quality 80
```

정상적으로 실행되면 Isaac Sim / OmniGibson 창이 열리고 `Merom_0_int` 스마트홈 씬이 로딩됩니다.

처음 로딩은 시간이 걸릴 수 있습니다. GPU와 디스크 상태에 따라 수 분 정도 걸릴 수 있습니다.

## 8. 실행 중 조작 방법

OmniGibson / Isaac Sim viewport가 활성화된 상태에서 키보드를 사용합니다.

### 8.1 기본 조작

```text
N: 새 resident/context episode 생성
T: 현재 시나리오에 매핑된 로봇 replay task 실행
R: 현재 task/run 중단 및 reset
I: interrupt/reset 계열 중단
F: 센서 감지 범위 시각화 On/Off
L: 센서 레이아웃 전환(current -> dense -> sparse)
K: 현재 센서 레이아웃 export
1: top overview camera
2: resident follow camera
3: free viewer camera
W/A/S/D: 이동 가능한 posture일 때 resident 이동
Q/E: resident follow mode에서 resident 회전
```

### 8.2 추천 검증 순서

가장 단순한 검증 순서는 다음과 같습니다.

1. 씬이 로딩될 때까지 기다립니다.
2. `N`을 눌러 resident scenario를 생성합니다.
3. top-view에서 resident 위치와 센서 context가 바뀌는지 확인합니다.
4. `F`를 눌러 센서 범위를 켜고 끕니다.
5. `L`을 눌러 센서 레이아웃이 바뀌는지 확인합니다.
6. `T`를 눌러 replay task를 실행합니다.
7. 로봇이 움직이는 동안 데이터가 저장되는지 확인합니다.
8. replay가 끝나거나 `R`을 눌러 run을 종료합니다.
9. `datasets/homesense_episodes/` 아래 새 run 폴더를 확인합니다.

## 9. 센서 레이아웃

시작 시 `--sensor-layout` 옵션으로 초기 레이아웃을 선택할 수 있습니다.

```text
current: 현재 데모용 표준 배치, 10개 모션 센서
dense:   고밀도 배치, 16개 모션 센서
sparse:  최소 배치, 4개 모션 센서
```

실행 중 `L` 키를 누르면 다음 순서로 전환됩니다.

```text
current -> dense -> sparse -> current
```

센서 범위는 `F` 키로 시각화할 수 있습니다.

## 10. 데이터셋 저장 구조

`N`은 resident/context episode를 준비하는 단계입니다. 이 단계만으로는 학습용 task-run 데이터셋이 생성되지 않습니다.

학습용 task-run은 `T`를 눌러 replay task가 시작될 때 생성됩니다. Replay가 끝나거나 `R` / `I`로 중단되면 해당 run이 닫히고 metadata와 HDF5가 저장됩니다.

출력 위치:

```text
datasets/homesense_episodes/
```

예시:

```text
datasets/homesense_episodes/
  session_20260616T000000Z_metadata.json
  session_20260616T000000Z_events.jsonl
  legacy/
    episodes_20260616T000000Z.jsonl
  run_20260616T000100Z_ep0002_activity_context_delivery_med_room_2_sc/
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
          frame_000000.jpg
          frame_000001.jpg
```

### 10.1 주요 파일 설명

```text
metadata/metadata.json
  해당 task-run의 환경, 로봇, task, scenario, replay, sensor layout, gym metadata

metadata/quality_report.json
  task 성공 여부, sub-goal 성공 여부, 최종 score, 카메라 누락률 등

data/steps.jsonl
  step 단위 robot state, action vector, resident context, sensor context

data/cameras/<camera_name>/*.jpg
  replay 실행 중 저장된 robot RGB camera frame

metadata/dataset.hdf5
  JSONL / metadata / quality report / step numeric array / camera path table을 포함한 HDF5 export
```

## 11. AI Gym metadata 확인

최종 제출 버전에서는 task-run metadata에 `gym_framework` 블록이 추가됩니다.

새 run을 하나 생성한 뒤 다음 명령으로 최신 metadata 파일을 찾습니다.

```bash
cd "$PROJECT_ROOT"
find datasets/homesense_episodes -path '*/metadata/metadata.json' | sort | tail -n 1
```

파일을 열면 다음과 유사한 블록이 있어야 합니다.

```json
"gym_framework": {
  "framework_id": "homesense_gym",
  "schema_version": "homesense_gym_v1",
  "registry_path": "smart_home/configs/gym_registry.yaml",
  "env_id": "merom_0_int_v09",
  "robot_id": "r1pro",
  "task_id": "medicine_delivery",
  "task_name": "deliver_item",
  "scenario_id": "watching_tv",
  "resident_zone_id": "living_room",
  "sensor_layout_id": "current",
  "policy_source": "teleoperation_replay",
  "replay_id": "delivery_med_room_2_sc",
  "evaluator_id": "medicine_delivery_v1",
  "dataset_format": "homesense_task_run_v1",
  "future_integrations": {
    "nvidia_cosmos": {
      "status": "todo",
      "interface": "video_action_context_label_export",
      "intended_use": "world_model_rollout_prediction_and_synthetic_variation_generation"
    }
  }
}
```

Registry 파일은 다음 위치에 있습니다.

```text
BEHAVIOR-1K/smart_home/configs/gym_registry.yaml
homesense-demo/smart_home/configs/gym_registry.yaml
```

이 registry는 현재 구현을 AI Gym Framework의 첫 번째 environment로 일반화하기 위한 선언형 목록입니다.

## 12. Task 성공 점수 계산 방식

현재 자동 라벨러는 `medicine_bottle_0` 약병 전달 task를 세 sub-goal로 나누어 평가합니다.

```text
grasp:     0.30
transport: 0.30
place:     0.40
```

최종 score는 다음 가중합입니다.

```text
score =
  0.30 * grasp_success
+ 0.30 * transport_success
+ 0.40 * place_success
```

각 sub-goal은 `True` 또는 `False`입니다.

```text
grasp:
  약병 cap point와 gripper point 사이 최소 거리가 0.18m 이하인 상태가 0.5초 이상 유지되면 성공

transport:
  약병 이동 속도가 0.025m/s 이상인 상태가 0.8초 이상 유지되면 성공

place:
  약병이 resident 주변 목표 영역 안에 있고 안정 상태가 1.2초 이상 유지되면 성공
```

세 sub-goal이 모두 성공해야 최종 `success=true`가 됩니다. 일부만 성공하면 `partial_or_failed`로 기록됩니다.

## 13. Replay와 시나리오 매핑

`N`으로 선택된 resident activity context에 따라 `T` 실행 시 replay가 자동 선택됩니다.

대표 매핑:

```text
arriving_home / watching_tv / resting_on_sofa -> delivery_med_room_2_sc
lying_in_bed                                -> delivery_med_room_3
toilet_use                                  -> delivery_failure_case
playing_piano                               -> delivery_med_room_1_v01_repaired_v09
```

주의:

- 일부 HDF5 replay는 기록 당시 scene registry와 현재 로딩된 scene registry가 정확히 맞아야 state replay가 가능합니다.
- 서로 다른 embedded scene으로 기록된 HDF5를 한 세션에서 섞어 state replay하면 UUID mismatch가 발생할 수 있습니다.
- 일반 검증은 기본 실행 명령 기준으로 진행하는 것을 권장합니다.

## 14. 자주 발생하는 문제와 해결 방법

### 14.1 `python.sh: No such file or directory`

Isaac Sim 설치 경로가 다릅니다.

해결:

```bash
export ISAAC_PYTHON=/actual/path/to/isaac-sim-5.1/python.sh
```

그리고 실행 명령에서 `"$ISAAC_PYTHON"`을 사용합니다.

### 14.2 `can't open file examples/smart_home/run_live_control_scene.py`

현재 작업 디렉토리가 잘못되었습니다. 이 스크립트는 `BEHAVIOR-1K` 안에서 실행해야 합니다.

해결:

```bash
cd "$PROJECT_ROOT/BEHAVIOR-1K"
```

그 뒤 실행 명령을 다시 입력합니다.

### 14.3 Conda 경고

가능하면 Conda를 끄고 실행합니다.

```bash
conda deactivate
```

### 14.4 Scene asset 관련 오류

다음 폴더가 있는지 확인합니다.

```bash
ls "$PROJECT_ROOT/BEHAVIOR-1K/datasets/behavior-1k-assets"
ls "$PROJECT_ROOT/BEHAVIOR-1K/datasets/behavior-1k-assets/scenes/Merom_0_int"
```

없다면 제출 압축이 불완전하게 해제되었거나, assets subset이 포함되지 않은 zip을 사용한 것입니다.

### 14.5 Replay 파일을 찾지 못함

다음 폴더를 확인합니다.

```bash
ls -lh "$PROJECT_ROOT/replay-data"
```

실행 명령의 `--hdf5-replay-dir`가 이 경로를 가리키는지 확인합니다.

### 14.6 GPU / 렌더링 / 창이 열리지 않는 문제

검증 PC에서 Isaac Sim이 단독 실행되는지 먼저 확인해야 합니다. Isaac Sim 자체가 실행되지 않으면 본 프로젝트도 실행되지 않습니다.

NVIDIA driver 상태 확인:

```bash
nvidia-smi
```

### 14.7 실행은 되지만 느림

본 프로젝트는 Isaac Sim / OmniGibson full scene을 사용합니다. 검증 환경에 따라 replay가 실시간보다 느릴 수 있습니다.

속도를 우선할 경우 카메라 저장을 끄고 실행할 수 있습니다.

```bash
cd "$PROJECT_ROOT/BEHAVIOR-1K"

TORCHDYNAMO_DISABLE=1 "$ISAAC_PYTHON" \
  examples/smart_home/run_live_control_scene.py \
  --scene-model Merom_0_int \
  --full \
  --cpu-dynamics \
  --robot-type R1Pro \
  --hdf5-replay-dir "$PROJECT_ROOT/replay-data" \
  --enable-activity-sensors \
  --sensor-layout current \
  --episode-log-dir "$PROJECT_ROOT/datasets/homesense_episodes" \
  --step-log-hz 2.0
```

이 경우 `data/cameras/` 이미지 저장은 생략될 수 있습니다.

## 15. 주요 폴더 설명

```text
BEHAVIOR-1K/
  실제 OmniGibson 실행용 working tree입니다.

BEHAVIOR-1K/examples/smart_home/
  실행 entrypoint가 들어 있습니다.

BEHAVIOR-1K/smart_home/
  HomeSense sensor, scenario, dataset logging, live control 구현입니다.

BEHAVIOR-1K/smart_home/configs/scenes/
  Merom_0_int scene profile과 sensor layout 정의가 있습니다.

BEHAVIOR-1K/smart_home/configs/gym_registry.yaml
  AI Gym Framework 확장을 위한 registry입니다.

homesense-demo/
  GitHub 공유용 코드 mirror입니다.

replay-data/
  협업자가 수집한 HDF5 replay 데이터입니다.

datasets/homesense_episodes/
  실행 후 생성되는 task-run 데이터셋 출력 위치입니다.

docs/
  프로젝트 진행 문서, 발표 보조 문서, 데이터 schema 설명 문서가 있습니다.
```

## 16. 제출물에 포함되지 않는 것

다음 항목은 제출 압축에서 제외했거나, 제외하는 것이 정상입니다.

```text
Isaac Sim 본체
.git
.agents
.codex
node_modules
runtime logs
crash dump core 파일
임시 zip 파일
대량 생성된 run 데이터 일부
```

Isaac Sim 본체는 검증 PC에 별도로 설치되어 있어야 합니다.

## 17. 발표 / 검증 시 핵심 설명 문장

본 프로젝트는 특정 로봇 데모를 넘어, 스마트홈 디지털 트윈에서 robot, scene, task, resident scenario, sensor layout, evaluator를 조합해 학습 episode를 생성하는 AI Gym Framework의 prototype입니다. 현재 구현된 첫 번째 registered environment는 `Merom_0_int + R1Pro + medicine_delivery`이며, replay 실행 중 생성되는 video, action, context, quality label 데이터는 향후 NVIDIA Cosmos 같은 world model 기반 synthetic data generation / rollout prediction pipeline으로 확장할 수 있습니다.

