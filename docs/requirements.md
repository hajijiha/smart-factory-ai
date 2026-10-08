# 요구사항 추적 및 검증표

현재 아래 '구현' 표시는 코드 작성 상태다. '실측 대기'는 아직 완료/성능 통과를
주장하지 않는다. 데이터·모델·통합 테스트 결과가 존재해야 제출 가능한 상태다.

| 요구사항 | 구현 위치 | 검증 |
|---|---|---|
| Ubuntu 22.04 / Python ≤3.11 / PyTorch≥2 / CPU | Dockerfile 두 개 | 이미지 빌드·버전 확인 대기 |
| Docker Compose 단일 실행 | compose.yaml | 모델 포함 깨끗한 클론 테스트 대기 |
| Public GitHub / Conventional Commits | 저장소·Git 이력 | 공개 업로드 대기 |
| 아키텍처·1인 역할·모듈 문서 | README, docs | 작성 |
| Gazebo 3D + ROS2 | simulator world/plugin/bridge | 실제 카메라 취득 대기 |
| Fault Level 0–10 + 진동·불량 연동 | simulator/signal.py, bridge.py | 통제 실험 대기 |
| 정상 5,000 / 고장≥1,000 | generate | dataset.json 생성 대기 |
| 영상 조명·각도·배경 랜덤화 | random_scene | manifest·장면 확인 대기 |
| MQTT / UTC / JSON / 시간 동기화 | common, communication.md | MQTT·DB 통합 대기 |
| FFT / 시간5·주파수3 이상 특징 | pdm/features.py | 단위 테스트 대기 |
| 관리도 + 정상 학습 AE | pdm/model.py, train.py | 학습·F1 비교 대기 |
| Score [0,1] / HI [0,100] / 상태4개 | Detector | 교정 후 테스트 대기 |
| 경고·위험 전환 알람 | pdm/service.py | 통합 대기 |
| F1≥0.80 / CPU≤100ms / 5회 | pdm/train.py | 실측 대기 |
| YOLOv8 3개 클래스 파인튜닝 | vision/train.py | 학습 대기 |
| mAP50≥0.80 / CPU≥20FPS / 100장 | evaluate, inference | 실측 대기 |
| 실제 backbone Grad-CAM / 정상 비교 | vision/gradcam.py | 원본·CAM 해석 대기 |
| 불량 이미지·결과 DB 저장 | storage.py, schema.sql | BYTEA·결과 확인 대기 |
| 실시간 / 상관 / 알람 / 5초 내 갱신 | dashboard, analytics, static | 2초 UI 갱신 테스트 대기 |
| Pearson/Spearman·시차·한계 | analytics, integration.md | 통제 레벨 측정 대기 |
| 위험 컨베이어 실제 정지 | controller, C++ plugin | 실제 관절 속도 확인 대기 |
| TimescaleDB + PostgreSQL 최소 필드 | schema.sql | 스키마 생성·조회 대기 |
| 경량화 선택 과제 | ONNX export·비교 benchmark | 실측 대기 |
| LSTM RUL / PSI 선택 과제 | 범위 제외 | 미구현, 필수 아님 |
