# 요구사항 추적 및 실제 검증

| 요구사항 | 구현 | 실측 근거 |
|---|---|---|
| Ubuntu22.04/Python≤3.11/Torch≥2/CPU | Dockerfile 두 개 | hardware.json: Ubuntu22.04.5, Python3.10.12, Torch2.5.1+cpu |
| Compose 단일 실행 | compose.yaml, 실제 모델 포함 | 별도 공개 clone 검증은 deployment.json에 기록 |
| Public/Conventional Commits | hajijiha/smart-factory-ai | Public main에 source·모델·실측 결과 제공 |
| 구조·1인 역할·모듈 문서 | README/docs | 아키텍처 diagram과 담당·이론·학습·실험 상세 설명 |
| 실제 Gazebo3D/ROS2 | C++ plugin, SDF, 카메라/관절 취득 | 6,000 actual camera JPEG와 joint provenance |
| Fault0–10·진동/불량 연동 | bridge/signal | p=.02+.085level, 실제 반복 운전+위험 정지 |
| 정상5,000/고장≥1,000 | generate_sensor | 정상5,000+고장2,000 생성·전체 분리 확인 |
| 영상 조명·각도·배경 변화 | random_scene | 정상3,000+종류별1,000, 전체6,000중복0 |
| MQTT/UTC/JSON/동기화 | common, communication.md | 비동기 실제 DB 저장, event HI불일치0 |
| FFT/시간5·주파수3이상 | features.py | 실제 FFT,9특징, 물리 주파수·Nyquist 회귀 |
| 관리도/정상학습AE | model/train | F1 .995025/.988468, 독립test1,050개 |
| Score/HI/4상태/알람 | Detector/service | 경계·오류회귀, 실시간 상태 전환·알람 |
| F1≥.8/CPU≤100ms/5회 | pdm/train benchmark | FFT+모델 평균 .941083ms, 100창×5회 |
| YOLOv8 3종파인튜닝 | vision/train | 실제 train4,200장, 완료10epochs, validation-best |
| mAP50≥.8/CPU≥20FPS/100장 | evaluate/ONNX | testmAP.969052, ONNX66.261FPS, Torch25.446FPS |
| 실제 Grad-CAM/정상비교 | 최종SPPF Conv block postBN/SiLU | 실제gradient,4종영상+정직한배경집중한계 |
| 불량영상/결과DB | storage/schema | 3종JPEG BYTEA, path,bbox,conf,HI,CAM 실제조회 |
| 실시간/상관/알람/≤5초 | FastAPI/Canvas | 2초UI,실제스크린샷,프레임/CAM event정합 |
| Pearson/Spearman/시차/한계 | analytics | .527927/.564187, 시차5초최대.540091,387개실험 |
| 위험 실제컨베이어정지 | controller/C++ | 실제roller≈0, 위험reset거절,수동회복4rad/s |
| Timescale/PostgreSQL 최소필드 | schema.sql | 실제hypertable/columns/JPEG/HI쿼리검증 |
| ONNX 선택과제 | CPU export/동일100장이미지 | 두 엔진 box/class parity, 실측속도 비교 |
| LSTM RUL/PSI 선택과제 | 범위 제외 | 미구현, 필수 아님 |

목표 숫자를 측정값처럼 적지 않았다. CPU 수치는 지정 하드웨어와 합성 테스트
조건의 결과이며 실제 공장 일반화·정밀CAM분할·물리적원인지연을 보장하지 않는다.
