# 실제 이송 공장과 통합 웹 운영

`live-v1`은 Gazebo가 계산한 제품·롤러 관측을 한 웹앱에서 확인하는 첫 운영 장면이다. 기본 구성은 10개 dynamic 제품, 72개 롤러, 검사셀, 정상·불량 배출 구역과 실제 Gazebo 카메라 두 대다. 검사·진단 모델은 기존 CPU 모델을 유지한다. 이 문서는 동작과 재현 방법을 설명하며, 측정된 성능과 수용 기준의 판정은 [이번 결과 보고서](results/factory-web-v1/REPORT.md)에 별도로 기록한다.

시뮬레이터는 **ROS2 Humble**을 사용한다. source는 `osrf/ros:humble-desktop-full`, `rclpy` 취득 노드와 `gazebo_ros::Node` 플러그인에 연결된다. 기동·새 clone 검증에는 실제 이미지·ROS 프로세스 버전과 plugin source binding도 함께 기록한다.

## 실행과 화면

Docker Compose V2를 사용한다. Windows에서는 Docker Desktop의 WSL 통합 환경에서 저장소 루트로 이동한다. 아래 명령은 Bash/WSL 기준이다.

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=40 simulator pdm vision storage controller dashboard
```

초기 기동은 컨테이너 빌드와 ONNX export 준비 시간이 필요하다. 기본 URL은 `http://localhost:8080`이다. 종료할 때 `docker compose down`을 사용하면 기존 DB·MQTT 볼륨과 로컬 데이터가 유지된다.

| 주소 | 용도 |
|---|---|
| `/`, `/factory` | 공장 3D, 실제 제품 위치·단계, 설비 선택, 정지·안전 재시작, 전경·검사 카메라 |
| `/monitor` | HI·진동·FFT·RUL·PSI, 고장 주입, 검사 영상·Grad-CAM·알람·검사 이력 |
| `/api/world` | 마지막 Gazebo 관측과 연결·나이·지연 상태 |
| `/api/world/layout`, `/ws/world` | 공통 형상 정의와 최신 관측 WebSocket |
| `/api/products` | 제품 단위 검사·HI·모델·CAM·배출 증거 |
| `/api/camera/overview/stream`, `/api/camera/inspection/stream` | 실제 Gazebo JPEG 영상 스트림 |

공장·관제는 같은 서버의 독립 URL이다. 메뉴 이동·직접 접근·새로고침을 지원한다. 공장 화면에서 설비나 제품을 선택하고 관제 링크로 검사 증거를 확인할 수 있다. 3D는 확대·회전·이동, 전체·상부·검사셀·모터 시점을 제공한다. 페이지를 숨기거나 떠날 때 3D animation, polling, socket과 카메라 연결을 정리하고, 복귀할 때 현재 관측을 다시 받는다.

서버의 Gazebo 물리·Mesa/Xvfb 렌더링·AI는 CPU로 실행한다. 브라우저의 Three.js WebGL2 가속은 브라우저 환경에 따른다. WebGL2나 그래픽 context를 사용할 수 없으면 실제 전경·검사 영상으로 확인한다. 브라우저 FPS, 실제 camera FPS, detector FPS는 서로 다른 측정이다.

## 장면과 운영 모드

| 설정 | 기본값·허용 범위 | 의미 |
|---|---|---|
| `FACTORY_WORLD_MODE` | `live` | 실제 롤러 이송 운영 world 선택 |
| `FACTORY_POC_PRODUCTS` | `10`; `2`, `3`, `8`–`12` | 작은 물리 PoC 또는 운영 제품 수 |
| `FACTORY_LIVE_DEFECT_MODE` | `operational`, `balanced-evaluation` | 실제 운용 확률 또는 명시적 균형 평가 장면 |
| `LIVE_WEAR_SECONDS` | `0`; 교정된 `100`–`180`초 | 기본은 수동 고장 주입, 별도 합성 열화 시연은 [RUL 문서](prognostics.md) 참조 |
| `simulator.live_motor_rpm` | 약 `954.93`RPM | 동결된 v1 진단 모델의 관측 조건 |
| `simulator.live_step_size`, `live_solver_iterations` | `0.004`초, `30`회 | ODE 물리 설정; 변경 후 안정성과 부하 재검증 필요 |

`operational`에서 새 논리 제품을 공급할 때 고장 레벨에 따라 불량 확률 `0.02 + 0.085 × level`을 사용한다. 기존 제품의 결함을 검사 직전에 바꾸지 않는다. 확률은 생성 규칙이며 YOLO의 예측 결과가 아니다. 짧은 구간의 실제 검출 불량률과 일치할 필요가 없다.

`balanced-evaluation`은 정상·스크래치·찍힘·이물질이 반복되는 명시적 평가 모드다. 화면에는 평가 전용 상태를 표시한다. 이 모드의 불량률을 실제 운용의 발생률로 해석하지 않는다.

작은 물리 PoC를 실행하거나 기본 운영 구성으로 돌아갈 때 전체 Compose 환경을 같은 설정으로 기동한다.

```bash
FACTORY_POC_PRODUCTS=3 docker compose up -d --build
FACTORY_POC_PRODUCTS=10 FACTORY_LIVE_DEFECT_MODE=operational docker compose up -d --build
```

기존 `GENERATE_DATA=1`, `GENERATE_RUL=1`, `GENERATE_CAUSAL=1`은 운영 모드보다 우선하여 기존 데이터 생성 world를 사용한다. 원래 v1/r3 데이터·기준 성능과 이번 live world의 데이터·평가를 구분한다. 원본 생성·재학습은 [시뮬레이터 문서](simulator.md)와 `scripts/reproduce.sh`를 따른다. 운영 서비스를 켠 채 별도 취득 Gazebo를 중복 실행하지 않는다.

## 물리 이송과 제품 ID

제품은 질량·관성·collision·마찰을 가진 dynamic body다. 롤러 관절은 목표 `6 rad/s`의 ODE joint motor로 구동하고 제품은 롤러 접촉·마찰로 이동한다. 이송 중 제품 좌표나 속도를 별도 웹 타이머로 전진시키지 않는다. 실제 속도·잔류 이동은 관측된 값으로 확인한다.

검사 구역에 한 제품이 진입하면 전체 롤러를 일시 정지한다. 최소 안정화 시간과 낮은 실제 제품 속도를 확인한 뒤 실제 카메라 프레임을 취득한다. 센서·영상의 MQTT PUBACK을 모두 받은 후 검사 완료를 보내며, YOLO 결과는 같은 제품의 분류 명령으로 전달된다. `검사 대기`는 이 정상적인 검사 hold를 뜻하며 위험 정지와 구분한다.

분류 구역에서는 실제 YOLO 판정에 따라 제품 body에 횡방향 힘을 적용한다. 실제 좌표가 정상·불량 lane에 도달했는지는 별도 배출 관측으로 기록한다. 이는 단순화된 힘 actuator이며, 움직이는 기계식 pusher의 접촉·변형을 정밀 재현하지 않는다. 판정이 없으면 분류 힘을 적용하지 않고 중앙으로 진행하며 `outfeed_lane=unknown`으로 남긴다. 이 미분류 배출을 정상 lane이나 reject lane으로 집계하지 않는다. 판정은 있으나 lane 도달이 불확실하면 `unconfirmed`다.

배출 이후 투입 구역이 비어 있을 때 물리 body를 투입점으로 재배치한다. 이 부분은 명시적인 공급 lifecycle reset이다. 배출점에서 투입점까지의 물리 회송으로 해석하지 않는다.

| 식별자 | 역할 |
|---|---|
| `run_id` | Gazebo 프로세스 시작마다 새 epoch |
| `slot_id`, world object `id` | 재사용하는 물리 body, 예: `product_01` |
| `product_id` | 투입·재공급마다 새로 부여하는 논리 제품 serial |
| `inspection_id` | 검사 구역 진입마다 부여하는 고유 검사 ID |
| `event_id`, `sensor_event_id` | 검사 센서·영상·HI·YOLO·CAM 연결 UUID |
| `model_version` | 실제 배포 YOLO checkpoint의 SHA256 |

제품 색은 검사 전에는 중립색이며, 검사 후 실제 quality 증거의 `(run_id, product_id, inspection_id)`를 연결해 표시한다. `결함 미검출`은 detector가 해당 제품 ROI에서 결함을 검출하지 않았다는 뜻이다. 아직 quality가 없는 제품의 `미검사`와 구분한다. 시뮬레이터가 생성한 결함 class 참값은 공개 world/layout/카메라/AI payload로 전달하지 않는다.

## 시간·프레임·연결 상태

Gazebo의 `sim_time`, 물리 스레드의 UTC, 카메라의 `camera_sim_time`과 bridge 수신 UTC는 목적이 다르다. world timestamp는 Gazebo 물리 스레드의 `observed_wall_time_ms`에서 만든다. 카메라 UTC는 해당 ROS 영상 수신 시각이며 카메라의 원래 물리 시각은 ROS header의 simulation timestamp다. RUL의 가상 운전초도 별도 단위다.

검사 프레임의 pose는 유한 관측 buffer에서 같은 실행·제품·검사 ID의 두 world 관측 사이를 보간한다. 조건은 다음과 같다.

- 두 관측의 간격이 시뮬레이션 `0.10`초 이하이고 camera 시각을 양쪽에서 감싼다.
- 위치는 선형 보간, quaternion은 Slerp를 사용하며 외삽하지 않는다.
- 제품 재공급 경계를 가로지르는 보간과 다른 실행의 pose는 거부한다.
- 영상 수신 나이는 취득 생성 시 `2`초 이내, 센서 관절 관측은 camera 시각에서 `±0.15` 시뮬레이션 초 이내다.

`pose_alignment`에 관측 sequence·양쪽 시각·간격·보간 비율과 `approximate=true`를 기록한다. 이는 render-time pose를 직접 저장한 값이나 수동 픽셀 mask가 아닌 **관측에 근거한 근사 투영 라벨**이다. 정지 검사에서도 수 픽셀의 기하학적 경계 오차가 있을 수 있다. `pose_sim_time == camera_sim_time`은 보간을 그 시각에서 평가했다는 뜻이며 정확한 원시 pose의 증명이 아니다.

웹은 짧은 두 관측 사이만 보간한다. 서버 world cache는 최신 한 상태를 저장하고 중복·역순 sequence와 이미 종료한 epoch를 거부한다. world가 2초 넘게 오래되거나 MQTT가 끊기면 stale을 표시한다. 웹의 갱신 중단은 더 보수적인 1.5초 freshness 기준을 사용한다. 연결 단절 시 마지막 화면을 멈추고 `관측 지연`을 표시하며, 이것을 실제 물리 정지 확인으로 표현하지 않는다.

실제 카메라도 별도 timestamp·지연 표시가 있다. 3D와 영상은 독립적인 관측 시점을 가지므로 화면끼리 비교할 때 각 시각을 확인한다. 재연결은 최신 snapshot으로 동기화하고 새 epoch에서는 이전 camera·안전 관측을 제거한다.

## 정지와 안전 재시작

운영자 정지 요청은 MQTT 접수 응답 뒤 실제 world의 `running=false`, 롤러 속도와 제품 잔류 이동으로 확인한다. PdM의 위험 상태도 롤러 목표 속도를 0으로 바꾸며 latch를 유지한다. 진단 시험대 모터는 회복 관측을 위해 계속 회전한다.

bridge는 MQTT 단절, ROS world 관측 2초 초과 지연, 중요한 발행의 PUBACK 실패 또는 lifecycle backlog를 감지하면 MQTT에 의존하지 않는 직접 ROS 명령으로 라인을 정지한다. 이전 retained start로 자동 복구하지 않으며 정지 이후 새로 발행된 수동 재시작을 요구한다.

재시작은 dashboard와 독립 controller가 다시 확인한다.

1. 고장 레벨을 `0`으로 낮춘다.
2. 현재 실행의 최근 진단에서 `status=normal`, `80 ≤ HI ≤ 100`인지 확인한다.
3. 최근 world는 2초 이내, health·line은 5초 이내이며 실제 롤러 속도가 `|ω| ≤ 0.01 rad/s`인지 확인한다.
4. `안전 재시작`을 누르고 실제 롤러 가동 관측까지 기다린다.

위험·지연·다른 실행의 관측에서는 재시작이 거절된다. 레벨 0으로 회복해도 자동 재시작하지 않는다. 명령 접수와 실제 정지·가동은 별도 상태로 표시한다. 요청 후 적용 확인에 시간이 걸리면 현재 관측과 알람을 먼저 확인한다.

MQTT QoS 1은 중복 전달을 허용한다. 중요한 센서·영상 batch는 동일 ID·bytes로 재시도하고 이미 PUBACK 받은 부분은 다시 발행하지 않는다. sparse lifecycle은 PUBACK 후에만 bridge 큐에서 제거한다. DB의 event·제품 identity 제약으로 중복 집계를 막는다. 이 보장은 살아 있는 bridge 프로세스의 재연결 범위다. bridge 강제 종료 시 메모리의 미완료 batch를 복구하는 영속 outbox는 이번 버전에 포함되지 않는다. PUBACK은 broker 인수 확인이며 AI 처리·DB commit 완료 확인과 구분한다.

## 진단 범위와 기준 보존

live 진단 모터는 동결된 v1 모델의 실제 관측 조건인 약 `100 rad/s = 954.93 RPM`에 맞춘다. RPM 숫자만 바꿔 다른 운전 조건의 정확도를 주장하지 않는다. 진동은 실제 관절 속도·위상에 고장·노이즈 수식을 적용한 합성 신호이고, 실제 베어링 마모·접촉 파괴 모델은 아니다. RUL도 교정된 합성 열화 조건 밖에서는 실제 설비 수명으로 해석하지 않는다.

기존 `vision.pt`, `pdm.pt`, `rul.pt`, `drift-reference.json`과 원본 결과를 동결한다. 새 배경의 support 구조에서 발생한 검출은 원시 whole-frame 결과에 보존하고, 제품 분류에는 **참값을 사용하지 않고 관측 pose로 투영한 제품 ROI 안에 중심이 있는 box**를 사용한다. 원시 whole-frame 성능과 제품 ROI 성능을 각각 평가한다. 이번 변경은 가중치 재학습·자동 교체나 강화학습 제어를 포함하지 않는다.

`product_history`는 기존 DB에 추가하며 기존 sensor·health·quality·CAM 테이블과 이미지 경로를 유지한다. storage가 기존 볼륨에도 필요한 테이블·열·index를 추가한다. health와 CAM이 quality보다 먼저 또는 나중에 와도 동일 event로 조정한다. 고빈도 world pose와 camera base64를 DB 전체 이력에 매번 저장하지 않는다. 현재 제품 cache는 bounded이며 영속 검사 이력은 DB에서 읽는다.

## 검증과 새 장면 평가 재현

아래 도구를 실제 전체 서비스와 함께 실행한다. 제어 검증은 라인의 고장 레벨·정지·재시작을 변경하므로 별도의 검증 운전에 사용한다. runtime observer 자체는 읽기 전용이다.

```bash
docker compose run --rm --no-deps pdm python3 -m pytest tests -q
docker compose run --rm --no-deps pdm python3 -m scripts.verify_factory_backend
docker compose run --rm --no-deps pdm python3 -m scripts.verify_factory_controls \
  --url http://dashboard:8080 --output /app/docs/results/factory-web-v1/controls.json
docker compose run --rm --no-deps pdm python3 -m scripts.verify_factory_runtime \
  --url http://dashboard:8080 --duration 1800 \
  --output /app/docs/results/factory-web-v1/stability-30min.json
```

30분 observer의 `passed`는 도구 자체의 기준이다. 계획의 8개 이상 제품·정지/복구 반복·중복 검사·소실·물리 배출·성능 기준은 원시 관측과 DB를 함께 검토해 판정한다. 화면 FPS와 관측-to-display 지연은 observer에서 측정하지 않으며 실제 브라우저의 `window.factoryDiagnostics()`와 화면 캡처로 별도 확인한다. CPU 부하는 Gazebo·AI·웹·카메라 동시 실행 조건을 기록한다.

새 장면 detector 평가는 기본 모델을 바꾸지 않고 새 균형 운전 한 번의 **첫 100개 고유 검사**를 고정한다.

```bash
FACTORY_POC_PRODUCTS=10 FACTORY_LIVE_DEFECT_MODE=balanced-evaluation \
  docker compose up -d --force-recreate simulator
```

새 `run_id`는 `/api/world`의 `state.run_id`에서 확인한다. `data/factory-v1-evaluation/manifest.jsonl`에 해당 run의 100개 검사가 취득되고 실제 quality 증거가 저장된 뒤 아래 `ACTUAL_RUN_ID`를 교체해 실행한다. checkpoint hash는 [기준 snapshot](results/factory-web-v1/baseline.json)의 사전 기록값을 사용한다.

```bash
docker compose run --rm --no-deps pdm python3 -m scripts.evaluate_live_factory \
  --run-id ACTUAL_RUN_ID \
  --expected-model-sha256 de2b27a91410cd0ee12167a43b9651a9ef06198e6c86d80fdd2e9d84dffcde57 \
  --workload-label "Integrated factory services active"
FACTORY_POC_PRODUCTS=10 FACTORY_LIVE_DEFECT_MODE=operational \
  docker compose up -d --force-recreate simulator
```

평가 도구는 frame hash, 배포 model hash, 제품·검사·카메라 identity, 실제 AI 입력 bytes, ONNX export binding과 frame freshness·근사 보간·PUBACK 기록을 검사한다. `new-world-selection.jsonl`, `new-world-vision.json`, latency CSV와 그림을 `docs/results/factory-web-v1/`에 출력한다. 클래스별 AP, 원시 전체 영상 AP, 제품 ROI AP와 운영 threshold의 정상 오검출을 함께 읽는다. 단독 detector benchmark는 별도로 부하 조건을 바꾸어 측정하고 결과를 다른 파일로 보존한다.

100장 CPU FPS 범위는 전처리·추론·NMS이며 JPEG decoding, ROI filter, CAM, MQTT, DB와 browser frame rate는 제외한다. 기존 v1의 높은 F1/mAP/FPS를 이번 장면의 결과로 재사용하지 않는다. 새 clone·빈 data·새 DB·ONNX 미생성 조건의 한 Compose 기동도 기존 볼륨 기동과 별도 증거로 확인한다.

## 최종 측정과 운영 복구

[첫 버전 결과](results/factory-web-v1/REPORT.md)에 30분 운전·새 장면 고정100장·동시/단독 CPU·273개 회귀·빈 data/새 DB clone·독립 검토를 연결했다. 평가 run과 일반 운영 run을 구분하고, 최종 복구는 [runtime-final.json](results/factory-web-v1/runtime-final.json)을 확인한다.

실측 realtime factor는 약0.85였으며 전경 카메라는 간헐적으로2초 freshness 기준을 넘었다. 이때 UI는 지연으로 표시한다. 검사 영상·world pose의 속도와 웹 렌더링 FPS는 별개다. WebGL fallback은 production JS의 분기 검사까지 확인했고 실제 GPU context failure/복구는 미시험이다.

수신 완료한 cache 관측의 age는 수신 당시 source delay와 monotonic 경과시간으로 계산한다. 실제 수신 당시 future·stale 관측은 거절한다. controller의 일부 reset 경로에는 기존 UTC freshness 확인이 남아 있으므로 모든 재시작 경로가 시계 보정에 면역이라고 해석하지 않는다.

## 첫 버전의 경계와 후속 단계

이번 버전은 한 생산라인과 한 제품 형상, 세 가지 단순 결함 형상을 사용한 정지 검사다. 연속 이동 검사, 가림·motion blur·반사·저대비의 대표성, RPM·하중·시동 조건의 PdM 강건성, 약한 고장 개선, 실공장 정확도·물리 교정은 별도 시험이 필요하다.

`/analysis`, `/models` 독립 페이지, 사건 재생·전후 비교, 여러 라인, 영속 delivery outbox와 장애 복구 확장, RUL 오차 구간, 모델 승격 절차, Gazebo 후속 버전 이전은 후속 단계다. 기존 관제에 남아 있는 검사 이력·분석 기능은 유지하지만 이 후속 페이지들이 완성됐다는 의미는 아니다. 장면의 랙·펜스·제어반은 정적인 시각 구성이고 별도 가동 설비로 집계하지 않는다.
