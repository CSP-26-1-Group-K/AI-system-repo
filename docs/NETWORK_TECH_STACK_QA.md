# Network Tech Stack & Q&A Notes

이 문서는 발표 질의응답 대비용으로, 현재 프로토타입에서 실제 구현된 네트워크 구성과 최종 서비스 아키텍처에서 확장 예정인 구성을 구분해 정리한다.

## 1. Current Prototype Network Stack

현재 구현은 **DGX / 서버 머신에서 OmniGibson + FastAPI 서버가 실행되고, Electron 클라이언트가 네트워크로 접속하는 구조**다.

```text
Electron Client
  ├─ HTTP GET /health, /state
  ├─ HTTP POST /command/*
  ├─ WebSocket /ws
  └─ MJPEG GET /video.mjpg

FastAPI Live Server
  ├─ OmniGibson / Isaac Sim main loop
  ├─ Command Queue
  ├─ Sensor State Encoder
  └─ Camera Frame Capture
```

### Server-Side Stack

- **FastAPI**
  - REST command API, health check, state snapshot API, MJPEG stream, WebSocket endpoint를 제공한다.
  - Python 기반이라 OmniGibson / Isaac Sim Python runtime과 같은 프로세스 또는 같은 실행 환경에서 붙이기 쉽다.

- **Uvicorn / ASGI**
  - FastAPI 앱을 구동하는 비동기 서버 런타임이다.
  - WebSocket과 HTTP streaming을 함께 처리할 수 있다.

- **HTTP REST**
  - 사용 목적: 명령성 요청 처리
  - 예시:
    - `POST /command/set-human-input`
    - `POST /command/rotate-human-heading`
    - `POST /command/set-camera`
    - `POST /command/set-video-source`
    - `POST /command/set-sensor-ranges-visible`
    - `POST /command/run-task`
    - `POST /command/reset`
  - 이유: 카메라 변경, Task 실행, 이동 입력처럼 명령의 성공/실패 응답이 필요한 제어에는 REST가 단순하고 디버깅이 쉽다.

- **WebSocket**
  - 사용 목적: 실시간 상태 push
  - Endpoint: `WS /ws`
  - 서버가 약 0.1초 주기로 현재 상태 snapshot을 클라이언트에 broadcast한다.
  - 전달 내용:
    - 거주자 위치 / 방향 / 감지 zone
    - 활성 모션 센서
    - 압력 센서 상태
    - 로봇 상태
    - 카메라 상태
    - 영상 frame id
  - 이유: 클라이언트가 `/state`를 계속 polling하는 것보다 상태 변화 반영이 빠르고 구조가 명확하다.

- **MJPEG over HTTP**
  - 사용 목적: 시뮬레이터 카메라 영상 스트리밍
  - Endpoint: `GET /video.mjpg`
  - MIME type: `multipart/x-mixed-replace`
  - 각 frame은 JPEG로 인코딩되어 연속 전송된다.
  - 이유:
    - 브라우저와 Electron에서 `<img>` 또는 image stream으로 쉽게 표시 가능
    - WebRTC / Omniverse Streaming 없이 구현 가능
    - 데모 검증용으로 충분히 단순하고 안정적
  - 한계:
    - 압축 효율과 지연 시간은 WebRTC보다 불리할 수 있음
    - 고해상도 / 고FPS에는 부적합

- **JSON Payload**
  - REST command body와 `/state`, WebSocket 상태 전송은 JSON 기반이다.
  - 이유: 구조가 단순하고 디버깅이 쉬우며, `curl`이나 브라우저 devtools로 바로 확인 가능하다.

### Client-Side Stack

- **Electron**
  - 데스크톱 앱 형태의 클라이언트.
  - MacBook / Windows / Linux 배포 가능성을 고려한 선택이다.
  - 현재 클라이언트는 시뮬레이터를 직접 실행하지 않고, 서버 URL에 접속해 영상과 상태를 받아온다.

- **Chromium Fetch API**
  - `/health`, `/state`, `/command/*` 호출에 사용한다.
  - 연결 시 health check 후 MJPEG stream과 WebSocket을 연다.

- **Browser WebSocket API**
  - `/ws`에 연결해 상태 업데이트를 수신한다.
  - 연결이 끊기면 1초 뒤 재연결을 시도한다.

- **MJPEG Image Stream**
  - Electron 화면의 중앙 영상 영역은 서버의 `/video.mjpg`를 직접 표시한다.
  - 카메라 source가 바뀌어도 같은 stream endpoint를 유지하고, 서버 내부에서 viewer / robot camera frame source만 바꾼다.

## 2. Runtime Communication Flow

### Initial Connection

```text
1. Electron app starts
2. User enters server URL
3. Client calls GET /health
4. Client calls GET /state once
5. Client opens GET /video.mjpg
6. Client opens WS /ws
```

### Resident Movement

```text
1. User presses WASD / QE
2. Electron computes movement vector or heading delta
3. Client sends POST /command/set-human-input or /command/rotate-human-heading
4. FastAPI handler puts command into OmniGibson command queue
5. Simulator main loop applies movement safely
6. Sensor state is recomputed
7. Server broadcasts updated /state snapshot over WebSocket
```

### Camera Switching

```text
1. User selects camera from dropdown
2. Client sends:
   - POST /command/set-video-source
   - POST /command/set-camera
3. Simulator updates viewer camera or robot RGB source
4. /video.mjpg continues streaming frames from the selected source
5. /ws broadcasts current camera mode
```

### Sensor Update

```text
1. Resident position changes in simulator
2. Motion sensor rig evaluates FOV + distance + wall occlusion
3. Active sensor and resident zone are selected
4. Robot context is updated:
   - resident_zone
   - last_known_resident_zone
   - resident_position
   - active_motion_sensor
5. Client receives updated state through WebSocket
```

## 3. Why Not Omniverse Streaming?

현재 목표는 “시뮬레이터 viewport 자체를 원격 조작하는 것”이 아니라, **데모 앱에서 필요한 카메라 영상과 상태/명령만 주고받는 것**이다.

따라서 현재 프로토타입에는 Omniverse Streaming이 필수는 아니다.

- 영상: simulator camera frame을 JPEG로 읽어서 MJPEG로 전송
- 상태: WebSocket으로 JSON 전송
- 제어: HTTP command API로 전송

이 방식은 공식 Omniverse Streaming 지원 여부와 무관하게 동작한다. 다만, 최종 목표가 “Isaac Sim viewport를 원격 데스크톱처럼 고품질/저지연으로 직접 보는 것”이라면 WebRTC 또는 Omniverse Streaming 계열 기술이 더 적합할 수 있다.

## 4. Current vs. Planned Network Architecture

### Currently Implemented

- FastAPI server
- Uvicorn ASGI runtime
- HTTP REST command endpoints
- WebSocket state broadcast
- MJPEG camera stream
- Electron desktop client
- JSON-based state and command schema
- Local or LAN-based server URL connection

### Planned / Not Yet Implemented

- MQTT or RabbitMQ event broker
- Kafka-style event streaming
- Kubernetes-based microservice deployment
- Cloud storage integration
- Authentication / authorization
- TLS termination / HTTPS
- Multi-user session management
- Real replay execution service
- Model inference service connection

발표에서는 이 둘을 명확히 분리해서 말하는 것이 좋다.

## 5. MSA Perspective

현재 프로토타입은 하나의 FastAPI 서버 안에 여러 기능이 묶여 있지만, 서비스 관점에서는 다음과 같이 분리 가능한 구조다.

```text
Client App
  ↓ HTTP / WS / MJPEG
Live Gateway API
  ↓ command queue
Digital Twin Runtime
  ↓ state snapshot
Sensor Encoder
  ↓ context JSON
Robot Task / Replay Service
  ↓ future
Policy Inference Service
```

### Service Boundary

- **Live Gateway API**
  - 클라이언트 연결, 명령 수신, 상태 송신 담당

- **Digital Twin Runtime**
  - OmniGibson / Isaac Sim 실행, scene state 업데이트 담당

- **Sensor Encoder**
  - 거주자 위치와 센서 배치 기반으로 sensing state 계산

- **Robot Context Adapter**
  - sensing state를 로봇 task / replay / inference 입력 형식으로 변환

- **Robot Task / Replay Service**
  - 현재는 placeholder
  - 추후 협업자 replay 데이터 import 후 실제 task 실행과 연결

- **Policy Inference Service**
  - 현재 발표 범위에서는 계획 단계
  - 추후 Pi0.5 / WB-VIMA / LeRobot 기반 추론 서버와 연결 가능

## 6. Expected Q&A

### Q. 왜 WebSocket과 REST를 둘 다 사용했나?

REST는 명령 요청에 적합하고, WebSocket은 상태 push에 적합하기 때문이다.  
카메라 변경, 이동 입력, task 실행은 성공/실패 응답이 있는 명령이므로 REST로 처리했다. 반면 센서 상태와 로봇 상태는 계속 변하므로 WebSocket으로 주기 broadcast한다.

### Q. 영상도 WebSocket으로 보내면 안 되나?

가능은 하지만 현재는 MJPEG가 더 단순하다.  
Electron과 브라우저에서 바로 표시 가능하고, 디버깅도 쉽다. WebSocket binary frame이나 WebRTC는 더 복잡하며, 현재 검증 목표에는 과하다.

### Q. MJPEG는 지연이 크지 않나?

WebRTC보다 효율적이지는 않다.  
하지만 현재 데모는 고FPS 원격 조작보다, 카메라 전환과 센서 반응을 시각적으로 검증하는 목적이므로 MJPEG로 충분하다. 필요하면 추후 WebRTC로 교체할 수 있다.

### Q. 서버와 클라이언트가 다른 노트북이어도 동작하나?

가능하다.  
서버를 `--host 0.0.0.0`으로 실행하고, 클라이언트가 같은 네트워크에서 `http://SERVER_IP:PORT`로 접속하면 된다. Electron 클라이언트는 BEHAVIOR / OmniGibson을 설치할 필요 없이 서버 URL만 알면 된다.

### Q. 현재 구조가 MSA라고 할 수 있나?

현재 프로토타입은 엄밀히 말하면 단일 FastAPI live server 중심의 통합 프로토타입이다.  
다만 API 경계가 이미 command, state, video, sensor context로 분리되어 있어 추후 MSA로 분리하기 쉬운 구조다. 발표에서는 “MSA target architecture를 검증하기 위한 integrated prototype”이라고 설명하는 것이 정확하다.

### Q. MQTT, Kafka, RabbitMQ는 현재 쓰고 있나?

현재 프로토타입에는 아직 적용하지 않았다.  
지금은 단일 사용자 데모이므로 FastAPI + WebSocket으로 충분하다. 다중 센서, 다중 로봇, 다중 사용자, 로그 replay가 필요해지는 단계에서 MQTT/RabbitMQ/Kafka 같은 broker를 도입할 계획이다.

### Q. 보안은 어떻게 처리하나?

현재는 내부 데모용이므로 인증과 TLS는 구현하지 않았다.  
실서비스 또는 외부 네트워크 배포 시에는 HTTPS, access token, reverse proxy, 방화벽, 사용자별 세션 분리가 필요하다.

### Q. 왜 클라이언트에서 직접 OmniGibson에 붙지 않나?

OmniGibson / Isaac Sim은 무겁고 GPU 환경 의존성이 크기 때문이다.  
따라서 DGX 서버에서만 시뮬레이터를 실행하고, 클라이언트는 영상과 상태만 받아보는 thin client 구조가 배포와 운영에 유리하다.

## 7. Slide-Friendly Summary

발표 슬라이드에는 아래 정도로 압축해서 넣으면 된다.

- **FastAPI Gateway**: command API, state API, video stream endpoint 제공
- **WebSocket State Stream**: 센서 / 거주자 / 로봇 상태를 10Hz 수준으로 push
- **MJPEG Camera Stream**: simulator camera frame을 HTTP stream으로 전송
- **HTTP Command Channel**: 거주자 이동, 카메라 전환, task 요청 처리
- **Electron Thin Client**: DGX 서버에 접속해 영상 표시와 제어 입력만 수행
- **Future Extension**: MQTT/RabbitMQ event broker, replay service, inference service, K8s deployment로 분리 예정
