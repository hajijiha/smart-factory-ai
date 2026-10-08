# 통합 관제·저장·라인 안전 제어

## 저장 스키마

`docker/schema.sql`에서 TimescaleDB 확장을 활성화한다.
센서 `sensor_readings`는 timestamp로 분할한 hypertable이며
timestamp, sensor_id, vibration_x/y/z, temperature, fault_level, rpm,
event_id, JSON 관측값을 저장한다. 전체 2,048점 파형은 MQTT로 PdM에
전달하지만 DB에는 특징과 FFT 결과를 `health_readings.data`에 보관한다.

PostgreSQL의 `quality_inspections`에는 id, timestamp, image_path,
defect_type, confidence, bbox, health_index_at_time, gradcam_path,
image_bytes, data가 있다. 복수 결함은 검사 이벤트 한 개 아래 여러 행으로
저장하고 불량률 계산에서는 DISTINCT event_id를 사용한다.
이벤트가 중복 전달되어도 센서 PK·건강/검사 event PK로 중복 기록을 막는다.
경고/위험 전환은 alarms, 최신 Gazebo 관측은 line_status에 저장한다.

## 화면과 제어

FastAPI 읽기 API가 DB를 조회하고 브라우저는 2초마다 snapshot을 갱신한다.
실시간 RMS·온도·HI·스펙트럼·검사/불량 건수·영상·알람을 한 화면에 보여준다.
Canvas로 그래프를 그리므로 외부 CDN이나 유료 Vision 서비스가 필요 없다.

고장 명령은 대시보드 → MQTT → 시뮬레이터로 전달한다.
위험 인터락은 PdM → MQTT health → 독립 controller → MQTT conveyor →
ROS2 scene command → Gazebo physics thread로 전달한다.
controller가 실제 위험 진단을 받으면 `running=false`를 retained로 발행한다.
Gazebo 롤러 관절 속도 목표가 4 rad/s에서 0으로 바뀌며 실측 관절 속도를
line 이벤트로 다시 보여준다. UI의 문자열만 정지로 바꾸는 구현이 아니다.
수동 재시작은 최근 정상 HI(≥80)를 조건으로 한다. 위험 상태의 reset은
알람으로 거절한다. 복구해도 자동으로 재시작하지 않는다.

## 상관과 시차

센서와 품질을 event_id로 연결한 뒤 5초 구간의 평균 RMS와 불량 검사 비율을
구한다. 실제 검사 2개 이상인 구간만 사용한다. 검사하지 않은 정지 구간은
정상 제품 0개 불량처럼 분모에 넣지 않는다.
표본이 4개 구간 미만이거나 한 값이 상수면 상관계수를 NULL로 표시한다.

Pearson은 선형 관계, Spearman은 순위에 기반한 단조 관계를 측정한다.
0–30초(5초 단위)의 양의 시차에서 `RMS(t)`와 `defect_rate(t+lag)`를
비교한다. 표본 개수를 함께 남기며 가장 큰 값만 인과적 지연이라고 해석하지
않는다. 버킷 간 공백이 존재할 때의 정확한 시간 정렬은 별도 검증 대상이다.

생성기의 fault_level이 진동과 불량 확률에 공통으로 영향을 주므로 합성 환경의
관련성은 설계상 존재한다. 통제된 여러 레벨 실험으로 이 관계를 확인할 수
있지만 상관계수만으로 실제 공장 설비가 불량의 원인이라고 증명할 수 없다.
환경 요인·공정 부하·제품 종류가 달라지는 실제 데이터에는 교란변수와
공통 원인, 모델 오검출, 시간 누락이 영향을 준다.

## 실제 통합 검증

`python3 scripts/verify_runtime.py`는 UI API에 실제 명령을 보내고 다음을 확인한다.

1. 레벨 0에서 정상 HI와 센서/검사 이력 증가.
2. 레벨 10에서 위험 HI, 인터락, 실제 Gazebo 롤러 속도 절댓값 <0.01.
3. 위험 상태 reset이 거절되는지 확인.
4. 레벨 0 회복 후 수동 reset으로 실제 롤러 속도 >0.5를 확인.

실행 결과는 `docs/results/integration.json`에 저장한다.
컨테이너/모델/DB 준비 전에는 성공을 기록하지 않는다.
