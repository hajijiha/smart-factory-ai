# 구현 범위와 평가 기준

Gazebo 3D 장면에서 모터 상태와 카메라 영상을 취득하고, MQTT를 통해 설비 진단·품질 검사·라인 제어를 연결한다.

| 모듈 | 구현 | 평가 기준 |
|---|---|---|
| 실행 환경 | Ubuntu 22.04, Gazebo, ROS2 Humble, CPU | 카메라 영상·관절 상태 취득, Compose 기동 |
| 데이터 | 장면 랜덤화, 자동 라벨, 고장 주입 | 정상 센서 5,000개, 고장 2,000개, 영상 6,000장 |
| 예지보전 | FFT, 통계 관리도, 정상 학습 Autoencoder | test F1 ≥0.80, CPU ≤100 ms, 5회 측정 |
| 품질 검사 | YOLOv8n, Grad-CAM, ONNX | test mAP50 ≥0.80, 전처리 포함 100장 평균 ≥20 FPS |
| 관제·저장 | TimescaleDB, PostgreSQL, 대시보드 | 2초 갱신, 검사 이력, 상관·시차 분석 |
| 안전 제어 | MQTT 인터락, 수동 재시작 | 위험 시 Gazebo 롤러 정지, 정상 상태에서 재시작 |
| 재현 | 학습 모델, 생성 코드, 측정 기록 | 새 clone에서 단일 Compose 명령 실행 |
| 수명 예측 | 24초 이력 LSTM, 개체 단위 분할 | 12개 시험 개체, MAE/RMSE 및 상수 기준선 비교 |
| 분포 변화 | 정상 학습 기준 PSI, 128창 감시 | 고정 분포·극단 꼬리·온라인 알람 검증 |
| 원인 개입 실험 | 공통 조건의 Fault 0/5 쌍 | 60쌍, 개입 순서 무작위, paired bootstrap |

구현과 실험은 개인 프로젝트로 진행했다. 모듈별 설명과 평가 결과는 `docs/`에 있다.
선택 과제 세 가지인 LSTM RUL, ONNX CPU 배포와 PyTorch 비교, PSI 감시와 알람을 모두 구현한다.
각 평가의 합성 데이터 조건과 독립 검산 근거는 `docs/prognostics.md`와 `docs/results/completion-v3/`에 기록한다.
