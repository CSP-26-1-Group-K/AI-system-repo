# 4A. Project Prototype: Implementation

아래 내용은 `2026-C&S Project_Plan_Team-K ver.260525.pdf`의 11페이지  
`4A. Project Prototype: Implementation` 슬라이드에 넣기 위한 초안이다.

## Slide Text

### Prototype Implementation Details

**Digital Twin Runtime**
- OmniGibson / Isaac Sim 기반 `Merom_0_int` 스마트홈 Scene 로딩
- 실증 편의를 위해 내부 방문 제거 Scene Variant 사용
- R1Pro 로봇 Asset 및 거주자 Dummy Asset 배치

**Live Control Server**
- FastAPI 기반 제어 서버 구현
- `/video.mjpg`: 시뮬레이터 카메라 영상 실시간 스트리밍
- `/state`, `/ws`: 센서 / 거주자 / 로봇 상태 주기적 전송
- `/command/*`: 카메라 변경, 거주자 이동, Task 요청 처리

**Smart-Home Sensing**
- 공간별 모션 센서 배치 및 FOV 기반 감지
- 벽 충돌체 기반 Line-of-Sight 검사로 벽 너머 감지 방지
- 활성 센서와 예상 거주자 위치를 Robot Context로 변환
- 센서 감지 범위 On/Off 시각화 지원

**Client Application**
- Electron 기반 데스크톱 모니터링 앱 구현
- Top / Resident Follow / Robot Follow / Robot Camera 시점 전환
- WASD + QE 기반 거주자 이동 및 회전
- Task 선택 UI는 Replay 연동 전 Placeholder로 구현

## Recommended Layout

슬라이드에는 4개의 박스를 2x2로 배치하는 구성이 가장 깔끔하다.

```text
┌────────────────────────────┬────────────────────────────┐
│ Digital Twin Runtime       │ Live Control Server         │
│ - Merom_0_int              │ - FastAPI                   │
│ - R1Pro / Resident         │ - MJPEG / WebSocket / HTTP  │
└────────────────────────────┴────────────────────────────┘
┌────────────────────────────┬────────────────────────────┐
│ Smart-Home Sensing         │ Client Application          │
│ - Motion Sensor FOV        │ - Electron Desktop App      │
│ - Wall Occlusion           │ - Camera / Input / Task UI  │
└────────────────────────────┴────────────────────────────┘
```

## Shorter Version For Dense Slide

- `Merom_0_int` 기반 OmniGibson / Isaac Sim Digital Twin 구성
- R1Pro 로봇, 거주자 Dummy Asset, 스마트홈 모션 센서 배치
- FastAPI 서버가 영상 스트리밍, 상태 전송, 제어 명령을 중계
- Electron 앱에서 카메라 전환, 거주자 이동, 센서 범위 표시, Task 선택 수행
- 센서 감지 결과를 Active Sensor / Resident Zone / Robot Context로 변환

## Speaker Notes

이 프로토타입은 실제 모델 학습 이전에 서비스 동작 흐름을 검증하기 위한 시스템이다.  
로봇 정책 추론 자체보다는, 디지털 트윈 환경에서 거주자 위치 변화가 센서 상태와 로봇 입력 Context로 실시간 반영되는지를 검증하는 데 초점을 두었다.  
Task 실행 버튼은 현재 Replay 연동 전 단계이므로, 최종 시연에서는 협업자가 수집한 R1 Replay 데이터와 연결할 예정이다.
