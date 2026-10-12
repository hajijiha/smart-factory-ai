# 필수 요구사항과 선택 과제 최종 검증

2026년10월12–13일(한국 시각), 원 미션의 팀 역할·개인별 요약 요구를 제외해 검증했다.
필수53항목(기능41·환경5·제약7)과 선택3항목을 모두 구현하고 확인했다.
이 개수는 원문의 반복 조건을 제외한 항목 수이며 과제 점수나 모든 현실 조건의 정확도를 뜻하지 않는다.

처음 감사에서 미달이었던docstring·Conventional Commits와 부분 달성이었던검증 도구 경로 설정을 보완했다.
RUL·PSI를 추가하고 모델·기준분포를Git에 포함했다. 독립 검증자는 주 구현자와 별도로
RUL 원본·네트워크·PdM 혼동행렬·PSI 공식·실DB·새clone 기동을 검산했고 발견한 문제6개를 수정 확인했다.

기존 v1/r3 데이터·모델·평가는 보존했다. RUL80개 가상 개체와 원인 개입120장은 새 데이터 경로에 취득했다.
기준 모델로 확장 조건의 모든실패가 해결됐다고 주장하지 않는다.

## 주요 실행 근거

| 검증 | 결과 | 원본 |
|---|---|---|
| 전체회귀 |151passed,0failed,0skip(원본데이터읽기전용 연결) |[JUnit](results/completion-v3/tests.xml) |
| 독립새clone |9서비스한명령기동, 빈data·새DB·ONNX자동export;149pass·원본의존2예상skip |[기동](results/completion-v3/independent-clean-startup.json), [JUnit](results/completion-v3/independent-clean-tests.xml) |
| 독립PdM |관리도F1.995025 / AE.988468 |[검산](results/completion-v3/independent-required-model-review.json) |
| LSTM RUL |11,199창·80개체, 시험12개체/1,361시퀀스,MAE3.648초 vs상수31.369초 |[평가](results/completion-v3/rul.json), [독립원본](results/completion-v3/independent-data-review.json), [독립모델](results/completion-v3/independent-model-review.json) |
| 실제온라인 |RUL91건,MAE4.454초,PSI4.706경고DB→HTTP확인 |[원본](results/completion-v3/optional-runtime.json) |
| 시간·PSI 검산 |1/2/불규칙초간격의동일추세예측일치,독립PSI공식일치 |[검산](results/completion-v3/independent-runtime-review.json) |
| 물리인터락 |전후위험정지·거절·수동정상복구통과 |[검증](results/completion-v3/process-runtime.json), [복귀후](results/completion-v3/process-runtime-restored.json) |
| 최종DB |센서1,476행·세불량40행·CAM40건,HI누락/불일치0,이미지표본일치 |[DB](results/completion-v3/storage.json), [독립DB](results/completion-v3/independent-db-review.json) |
| 원인개입 |60쌍에서검출불량률+.333,95%CI[.217,.450] |[원본](results/completion-v3/root-cause.json) |
| 화면 |정상RUL조건대기·PSI.079안정,브라우저warning/error0 |[화면](results/completion-v3/dashboard-prognostics.png) |

## 필수53항목

| ID | 요구사항 | 판정 | 근거 |
|---|---|---|---|
| F01 | GitHub URL 형식 | 달성 | `https://github.com/hajijiha/smart-factory-ai` |
| F02 | 제출 저장소 Public | 달성 | 현재 저장소 메타데이터 visibility=public |
| F03 | Compose 한 명령으로 전체 실행 | 달성 | 독립 새clone의 빈data·ONNX없음·새DB에서9서비스 한명령기동; independent-clean-startup.json |
| F04 | Markdown에 아키텍처 다이어그램 | 달성 | README Mermaid에 독립 LSTM/PSI까지 포함한9서비스 구조 |
| F05 | docs에 모듈 학습·평가 문서 | 달성 | 기존 모듈 docs에 prognostics.md·root-cause.md·독립 검증 보고서 추가 |
| F06 | 프로젝트 개요 | 달성 | README와 docs/plan.md; 팀 관련 설명 요구는 제외 |
| F07 | Isaac Sim 또는 ROS2 호환 Gazebo | 달성 | Gazebo Classic11, ROS2 Humble, SDF·C++ 플러그인·카메라·관절 관측; CPU에서 새 기동 확인 |
| F08 | 고장 레벨 0–10 조절 | 달성 | FaultCommand의 범위 검증, MQTT fault 명령, bridge 적용; 실제0·3·10 관측 |
| F09 | 고장 증가 시 진동·불량 확률 함께 증가 | 달성 | 기존 주입 규칙 및60paired Fault0/5 개입; RMS/생성불량/검출불량95%구간이0초과 |
| F10 | 정상5,000·고장/불량1,000 이상 생성 | 달성 | 정상 센서5,000·고장2,000; 영상 정상3,000·불량 각1,000은 원문 영상 참고 목표도 충족 |
| F11 | 조명·각도·배경 랜덤화 | 달성 | 원본 manifest의 light/yaw/camera/background 변동, 테스트 범위 확대; random_scene 코드 |
| F12 | 센서 MQTT 또는 ROS2 발행 | 달성 | ROS2 관절 취득→MQTT sensor→PdM/저장, 현재 센서 DB 증가 |
| F13 | 토픽·JSON 필드·UTC 정책 docs 정의 | 달성 | communication.md에 prognostics/PSI topic, UTC·QoS·공백·순서·가상시간 정책 정의 |
| F14 | FFT 주파수 스펙트럼 출력 | 달성 | Hann/rFFT, health 메시지 spectrum, 실제 FFT 그래프 |
| F15 | 시간 특징5개 이상 | 달성 | mean, RMS, peak, crest factor, kurtosis |
| F16 | 주파수 특징3개 이상 | 달성 | dominant frequency, spectral energy, BPFO amplitude, BPFI amplitude |
| F17 | 관리도·Autoencoder 구현 | 달성 | Detector의 두 오차 계산; AE 구조9→16→3→16→9 |
| F18 | Anomaly Score 0–1 | 달성 | 보정 score 평균 및 clip; 경계 회귀 통과 |
| F19 | HI 0–100 | 달성 | `100×(1-score)`; 이번 정상·위험 실행 확인 |
| F20 | 정상·주의·경고·위험4단계 | 달성 | HI80/60/40 경계와 4상태 테스트 |
| F21 | 경고·위험 전환 알람 | 달성 | PdM 상태 전환 발행, DB의 danger·warning 및 기존 상태 전환 검증 |
| F22 | 두 알고리즘 P/R/F1 문서 비교 | 달성 | docs/predictive-maintenance.md와 pdm.json |
| F23 | F1≥0.80 | 달성 | 독립 모델/혼동행렬 재구현: 관리도.995025,AE.988468(test1,050); independent-required-model-review.json |
| F24 | CPU 추론≤100ms | 달성 | FFT·특징·두 모델·HI 평균1.327651ms 재측정 |
| F25 | 분할·하드웨어·5회 이상 반복 명시 | 달성 | 클래스별70/15/15, 하드웨어 문서·JSON, 100창×5회 원시 지연 |
| F26 | YOLOv8 또는 EfficientDet 파인튜닝 | 달성 | YOLOv8n, 자체 Gazebo train4,200장, validation fitness 선택, 학습 CSV·모델 카드 |
| F27 | 스크래치·찍힘·이물질3종 | 달성 | 세 클래스 AP, 현재 DB의 세 종류 검사·이미지·CAM |
| F28 | mAP50≥0.80 | 달성 | original test900장에서0.969052 재평가 |
| F29 | CPU 전처리 포함100장 평균≥20FPS | 달성 | 같은100장 전처리/NMS 포함: 단독측정ONNX70.495FPS·PT32.506FPS; 추가부하독립측정ONNX22.666FPS·PT5.134FPS(비교 조건 분리) |
| F30 | Grad-CAM 생성 | 달성 | 실제 forward/backward, gradient 평균 가중 ReLU; 정상·세 결함 재생성 |
| F31 | 불량 이미지와 결과 DB 저장 | 달성 | 최종 실제DB 세불량40행/CAM40건, BYTEA·disk표본sha일치; storage.json |
| F32 | 정상/불량 CAM 비교 해석 docs | 달성 | docs/vision.md의 정상 및 세 결함 비교, 배경 활성·8×8 해상도 한계 |
| F33 | 실시간 센서·HI 대시보드 | 달성 | 8084 실제관제·RUL조건대기·PSI안정화·검사증가; dashboard-prognostics.png 및독립새clone관측 |
| F34 | 진동·불량 빈도 상관 그래프 | 달성 | 실제 산점도, Pearson/Spearman·시차 표시 |
| F35 | 위험 시 컨베이어 자동 정지 | 달성 | 추가작업 전후 물리롤러정지·위험reset거절·정상수동복구 재통과; process-runtime*.json |
| F36 | 5초 이내 간격 자동 갱신 | 달성 | 정상 응답 후2초 polling, 약1초 센서 발행 및 현재 UI 자동 변경 확인 |
| F37 | 통계 방법·시차 결과·한계 docs | 달성 | docs/integration.md, analytics.py, correlation.json의0–30초 시차 |
| F38 | 센서 TimescaleDB 또는 InfluxDB | 달성 | 최종sensor_rows1,476 및sensor_hypertable=true; storage.json |
| F39 | 검사 이력 PostgreSQL 또는 MySQL | 달성 | PostgreSQL quality_inspections 관계형 테이블 |
| F40 | timestamp/sensor_id/vibration_x/y/z/temperature/fault_level | 달성 | 연속wear용fault_level DOUBLE PRECISION까지기존볼륨보존변환; 독립JSON/컬럼값불일치0 |
| F41 | id/timestamp/image_path/defect_type/confidence/bbox/health_index_at_time | 달성 | SQL 스키마·실제 저장·event_id HI 정합 |
| E01 | Ubuntu22.04 LTS | 달성 | 실행 컨테이너22.04 및 Dockerfile; WSL 호스트 버전과 구분 |
| E02 | Python3.8–3.11 | 달성 | 컨테이너3.10.12; WSL 호스트 Python3.12는 모델 실행 환경이 아님 |
| E03 | PyTorch≥2.0 | 달성 | CPU PyTorch2.5.1+cpu |
| E04 | Docker·Docker Compose | 달성 | 현재 Docker Engine29.2.1 및 Compose V2로 실행 |
| E05 | 전체 시스템 CPU 동작 | 달성 | CPU 모델 및Mesa/Xvfb Gazebo 포함9서비스; 독립새clone실행확인 |
| C01 | 직접 외부 데이터셋 학습 금지·자체 시뮬레이터 데이터 | 달성 | 생성 manifest/provenance와 학습 코드 경로; COCO 사전학습 가중치 초기화는 별도로 명시 |
| C02 | 서비스 간 직접 함수 호출 금지·MQTT/ROS2 비동기 | 달성 | 독립 PdM/Vision/storage/controller 프로세스와 MQTT 이벤트; Gazebo↔취득 ROS2. 같은 프로세스 내부 계산·공통 유틸리티는 서비스 간 호출로 계산하지 않음 |
| C03 | 유료 Vision SaaS 금지 | 달성 | 로컬 YOLO·ONNX·PyTorch; Google Vision/AWS Rekognition 호출 없음 |
| C04 | 파일 경로·IP를 환경 변수 또는 config로 분리 | 달성 | factory/settings.py에서YAML·env·local/container경로·DB·검증URL·출력분리; 공백경로보존; settings회귀 및실제호스트모듈실행 |
| C05 | 함수·클래스 docstring | 달성 | 모든production/tests Python 함수·클래스docstring AST누락0; code-quality.json |
| C06 | Conventional Commits | 달성 | 제출브랜치의전체이력Conventional형식통과; 과거공개원본은archive/pre-completion-20261013보존 |
| C07 | test를 학습과 다른 시나리오로 별도 생성 | 달성 | 기존독립seed/RPM/노이즈/카메라split 및RUL개체56/12/12사전분할; 독립11,199창·라벨·분할재검산 |

## 선택3항목

| ID | 선택 과제 | 판정 | 근거 |
|---|---|---|---|
| B01 |LSTM RUL 회귀 |달성 |run-to-failure80개체/11,199창,개체분리시험·온라인MQTT추론·UI·DB |
| B02 |ONNX와PyTorch 비교 |달성 |같은100장·warmup·CPU4threads·전처리/NMS·정밀도정합·부하조건별원시기록 |
| B03 |PSI 감시·알람 |달성 |정상train3,500개전용불변기준,극단꼬리포함,128창/32창평가,전환알람·DB·UI |

## 해석과 독립 검증

RUL은 단조 합성wear의 가상 운전초다. 물리적으로 파손된 실제 베어링 수명을 검증한 결과가 아니다.
정상·회복·불충분 이력에서는 유한 수명을 제시하지 않는다. PSI는 분포변화이며 고장확률·인과진단이 아니다.
60paired실험은 Fault_Level이 진동/불량을 조절하도록구현한시뮬레이터의구조적원인을확인한다.
실제공장일반화·저대비결함은미검증이고, 확장조건의기존/후보WARN은유효하다.

Vision 속도는 조건별로분리한다. CPU단독조건은ONNX70.495FPS/PT32.506FPS,
두Gazebo와검증작업이겹친독립부하조건은ONNX22.666FPS/PT5.134FPS다.
필수CPU20FPS는배포검출기ONNX가기준이며, 부하조건에서도PyTorch가20FPS를넘었다고주장하지않는다.
이수치는JPEG디코딩·CAM·IO·네트워크·DB를제외한개별검출기throughput이다.

[독립 검증 보고서](results/completion-v3/independent-review.md), [RUL·PSI 방법](prognostics.md),
[원인 개입 방법](root-cause.md), [강건성의 실패와 한계](bias-validation.md)에 자세한 근거를 남겼다.
원격게시·제출이력의최종검산은 별도 public-submission.json에 기록한다.
