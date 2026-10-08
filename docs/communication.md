# 비동기 통신과 시간 동기화

모든 업무 모듈은 독립 프로세스이며 MQTT QoS 1로 메시지를 주고받는다.
센서 서비스가 비전 함수를 직접 호출하거나 대시보드가 컨베이어 함수를 호출하지 않는다.
공통 유틸리티는 설정·직렬화·MQTT 연결에만 사용한다.
시뮬레이터 안의 Gazebo 플러그인과 취득 노드는 ROS2 토픽으로 연결한다.

| MQTT 토픽 | 발행 → 구독 | 주요 필드 |
|---|---|---|
| `factory/sensor` | 시뮬레이터 → PdM, 저장 | event_id, timestamp, sensor_id, vibration_x/y/z, temperature, fault_level, rpm, sampling_hz, waveform |
| `factory/image` | 시뮬레이터 → 비전 | event_id, timestamp, image_base64, sim_time |
| `factory/health` | PdM → 저장, 인터락 | event_id, timestamp, anomaly_score, health_index, status, features, spectrum, inference_ms |
| `factory/quality` | 비전 → 저장 | event_id, timestamp, image_path, defective, detections, inference_ms |
| `factory/gradcam` | 비전 → 저장 | event_id, timestamp, gradcam_path, metadata |
| `factory/alarm` | PdM, 인터락 → 저장 | timestamp, event_id?, source, severity, message |
| `factory/line` | 시뮬레이터 → 저장 | timestamp, fault_level, running, roller_velocity, motor_rpm, defect_probability |
| `factory/command/fault` | 대시보드 → 시뮬레이터 | timestamp, fault_level: 정수 0–10 |
| `factory/command/conveyor` | 인터락 → 시뮬레이터 | timestamp, running, reason, event_id? |
| `factory/command/reset` | 대시보드 → 인터락 | timestamp |

ROS2: `/factory/scene`(std_msgs/String JSON), `/factory/scene_ready`(동일 메시지+sim_time),
`/factory/inspection/image_raw`(sensor_msgs/Image), `/factory/inspection/camera_info`,
`/factory/motor_state`(sensor_msgs/JointState: motor_shaft, conveyor_roller).

UTC ISO 8601 예: `2026-10-08T12:00:00.123Z`.
시뮬레이터 취득 노드가 한 제품의 센서/영상에 같은 UUID `event_id`와 같은 UTC
`timestamp`를 부여한다. PdM·비전은 취득 시각을 바꾸지 않는다.
DB는 이 ID로 정확하게 연결하며 처리 완료 시각이나 가장 가까운 미래의 HI를
가져오지 않는다. 품질 이벤트가 먼저 오면 HI는 잠시 NULL이며 해당 건강 이벤트
도착 시 갱신된다. Grad-CAM도 이벤트 순서와 무관하게 조정한다.

Gazebo `sim_time`은 물리 시간이고 UTC와 별도 필드다. 실제 카메라 프레임은
장면 변경 확인 응답보다 최소 0.1 시뮬레이션 초 뒤의 프레임을 사용한다.
컨테이너가 같은 호스트 시계를 사용하므로 별도 호스트 간 NTP는 필요 없다.
외부 장비로 확장할 때는 NTP/PTP와 최대 허용 지연·누락 정책이 필요하다.

센서 메시지의 `waveform`은 2,048개 수치다. RMS 세 축은 이 가상 센서의
방향별 스케일이며 실제 측정 단위로 교정되지 않은 a.u.이다.
QoS 1 중복은 이벤트별 PK로 제거한다. 고장·컨베이어 명령은 retained로
재연결 후에도 적용한다. 운용 중 MQTT 브로커·DB 재시작 시 연결을 재시도한다.
이 구성은 localhost 학습용이며 MQTT 포트를 외부에 공개하지 않는다.
