# 공장 웹 첫 버전 구현·검증 보고서

작성일: 2026년 10월 13일, 한국 시각. 운영 world: `live-v1`. 기준 소스: `a82a325ab4cd8cf2ffb97cc9c81c24d1ec14d8f2`.

**상태: 최종 측정 대기.** 아래 구현·보존·예비 증거와 최종 수용 기준을 구분한다. `PENDING`은 미측정 또는 최종 원본 미확인이며 통과·실패를 뜻하지 않는다. 최종 검증자가 각 값을 원시 결과와 연결하고 실제 commit·run·설정·하드웨어를 기록한 뒤 판정을 갱신한다.

운영 절차와 재현 명령은 [공장 운영 문서](../../factory-live.md)에 있다. 계획은 workspace의 `FACTORY_WEB_IMPROVEMENT_PLAN.md`를 기준으로 한다.

## 구현 결과

| 범위 | 구현한 동작 | 증거·경계 |
|---|---|---|
| 한 웹앱의 두 페이지 | 기본 `/`·`/factory`, 기존 관제 `/monitor`, 공통 메뉴·직접 URL | 최종 desktop/mobile 브라우저 검증은 아래 별도 gate |
| 실제 제품 이송 | 기본 10개 dynamic plate, 72개 실제 ODE roller, 공통 layout | body 좌표·속도는 Gazebo 관측; 장시간 안정성은 최종 측정 대기 |
| 검사 lifecycle | 투입→이송→정지 검사→YOLO 판정→힘에 의한 횡분류→실측 배출 | 배출 후 공급 reset은 명시적 pose 재배치, 물리 회송은 구현하지 않음 |
| 실제 영상·웹 3D | 실제 Gazebo inspection/overview JPEG, 로컬 Three.js, 설비·제품 선택 | pose 보간은 측정한 두 관측 사이, WebGL 대체 영상 제공 |
| 제품 증거 연결 | run/product/inspection/event/camera/model identity, HI·CAM·outfeed 저장 | 미검사와 결함 미검출을 구분; 제품 중복 집계는 DB identity 제약 |
| 안전·freshness | 위험 인터락, 운영자 정지, 관측 지연, 직접 ROS 통신 정지와 수동 재시작 | PUBACK은 broker 확인; 실제 AI·DB 완료 및 프로세스 crash 복구와 구분 |
| 기존 기준 보존 | 네 모델·reference SHA256 유지, 원본 generation 분리, additive DB schema | 이번 장면 가중치 재학습·강화학습·모델 승격 없음 |

진단 모터는 동결 v1 관측 조건인 `100 rad/s ≈ 954.93 RPM`에 맞췄다. 롤러 setpoint는 `6 rad/s`다. 최종 물리 설정은 ODE `0.004`초 step, `30` solver iterations, realtime 목표 1이다. 이 값은 설정이며 실측 속도·realtime factor·지연의 결과값이 아니다. 진동은 실제 관절 관측에 수식을 적용한 합성 신호이며 실제 베어링 열화 역학은 모델링하지 않는다.

source의 transport는 **ROS2 Humble**이다. Dockerfile의 Humble 이미지·패키지, `rclpy` 취득 노드, `gazebo_ros::Node` 플러그인을 읽기 전용으로 확인했다. 최종 source·측정 image·프로세스의 binding은 새 clone 기동 증거와 함께 기록한다.

## 기준 보존과 main 정리

[baseline.json](baseline.json)은 구현 전 모델 hash와 당시 branch snapshot이다. 그 안의 과거 branch 이름을 현재 branch로 해석하지 않는다. 문서 작성 시 읽기 전용 hash 재확인에서 다음 네 파일은 baseline과 동일했다.

| 파일 | SHA256 |
|---|---|
| `vision.pt` | `de2b27a91410cd0ee12167a43b9651a9ef06198e6c86d80fdd2e9d84dffcde57` |
| `pdm.pt` | `f829b824123eee1adc66b2fd6cf4fb9a8c50ffd2ba8ea343c06730e93500bb73` |
| `rul.pt` | `602def1bf05519682fb97a0c7f41507d141aed920bd63f616af6a87a5b2ef323` |
| `drift-reference.json` | `8bc12684ec570284af367730e68b82d8a1b03ba922f5dbf46daaad5095eae45d` |

작업 저장소의 현재 source branch는 `main`이다. 읽기 전용 local ref 확인에는 `main`, `origin/main`과 아래 보존 tag가 남았다. 원격 게시·최종 tip 확인은 최종 검증 때 별도 기록한다.

| 보존 tag | 보존 commit |
|---|---|
| `archive/completion-20261013` | `a82a325ab4cd8cf2ffb97cc9c81c24d1ec14d8f2` |
| `archive/data-validation-20261013` | `24ca000b6ce8ba35024ac325264b89016a12697d` |
| `archive/process-control-20261013` | `7840522b75630513af640ef83afd19a3b1188004` |
| `archive/pre-completion-20261013` | `1b4dfba66ee5ffaefb112ea4ab9eec80cf920339` |

최종 commit: **PENDING**. 최종 공개 main/tag 검증 증거: **PENDING**.

## 현재 저장된 예비 증거

[frontend-api-final10-proof.json](frontend-api-final10-proof.json)은 `factory-1791826572366001`의 read-only API 표본이다. world sequence `4050→4073`, 10개 동일 제품 ID 모두의 양의 X 이동 약 `0.21744 m`, 실제 camera metadata와 `connected=true`, `stale=false`를 기록한다. 이 표본은 실제 관측 이동을 확인하지만 브라우저 렌더링·전체 이송 시간·30분 안정성·최종 0.004초 물리 설정의 반복 운전을 증명하지 않는다. 해당 run은 `balanced-evaluation`이었다.

[frontend-source-regressions.json](frontend-source-regressions.json)은 실제 production JS 함수를 Node VM과 고정 Three.js primitive로 실행한 검증이다. 문법, 과거 epoch snapshot에서 재시작 잠금, matching epoch 허용, stale 제어 잠금, 그려진 제품 ID·색과 미래 slot 재사용 분리, 다른 run/미검사 제품의 판정 색 방지를 통과했다. 이것은 실제 브라우저 WebGL·카메라 표시·mobile layout의 대체 증거가 아니다.

최종 검증 담당자가 전달한 최적화 구성의 첫 1분 관측은 source 상태 `10.63 Hz`, 물리 UTC→API 나이 p95 `114.5 ms`, realtime factor `0.5894`, 10개 body 모두 이동·integrity finding 0이었다. 이 값은 짧은 초기 관측이며 원본 파일 연결은 **PENDING**이다. API까지의 나이를 browser display 지연으로 해석하지 않는다. realtime factor 1은 목표 설정이며 이 측정은 실시간과 동일한 속도를 달성했다는 결과가 아니다.

독립 읽기 전용 코드 검토에서 두 문제를 발견하고 수정했다.

1. sensor/image 발행 성공을 확인하지 않고 검사 완료를 보내던 경로는 immutable batch의 개별 PUBACK 이후 완료하도록 변경했다. sparse lifecycle도 PUBACK 전에는 큐에서 제거하지 않는다. 연결·전달 실패는 직접 ROS 정지와 fresh manual resume latch로 처리한다.
2. 영상과 별도의 최신 pose를 결합하던 경로는 최대 0.10 시뮬레이션 초 간격의 같은 제품 관측 bracket에서 camera 시각의 pose를 보간하도록 변경했다. 2초 영상 수신 나이·±0.15초 센서 시각 차이·근사 여부와 ACK 시간을 기록한다. 평가 도구도 이 조건을 검증한다.

수정 후 코드 검토에서 두 경로의 보완을 확인했다. 실제 브로커 단절·장시간 운전·전체 성능은 최종 runtime 증거로 판정한다. `tests/test_live_protocol.py`는 보간·외삽 거부·slot reuse·stale/future frame·PUBACK timeout·partial retry·수동 재시작을 검증하도록 추가됐다. 최종 검증 담당자가 전달한 현재 회귀는 `217 passed, 2 skipped`이며 2개는 원본 DATA 미연결에 따른 예상 skip이다. 뒤이어 추가한 장애 검증 도구를 포함한 최종 재실행, 원본 DATA 연결과 JUnit 원본은 **PENDING**이다.

## 최종 실행 조건 기록

아래는 최종 결과를 채울 자리다. 예비 표본의 조건과 섞지 않는다.

| 항목 | 최종 기록 |
|---|---|
| source commit / tree diff hash | **PENDING** |
| 실행 UTC / 한국 시각 | **PENDING** |
| OS·WSL/Docker·CPU·logical cores·메모리 제한 | **PENDING** |
| CPU governor/host 전원 상태와 동시 workload | **PENDING** |
| Python·Gazebo·PyTorch·ONNX Runtime·browser version | **PENDING** |
| ROS transport / source·image·process binding | source ROS2 Humble 확인; 최종 runtime binding **PENDING** |
| stability run_id / defect mode / 제품 수 | **PENDING** |
| evaluation run_id / first100 selection hash | **PENDING** |
| 물리 step·iters / 측정 realtime factor | **PENDING** |
| 실제 desktop 해상도 / mobile viewport / WebGL 가속·대체 경로 | **PENDING** |

## 계획 수용 기준과 측정값

모든 목표는 동일 하드웨어의 최종 동시 실행 조건에서 확인한다. 기존 completion-v3의 결과는 비교 기준이며 이번 장면의 통과값으로 재사용하지 않는다.

| 계획 기준 | 목표·판정할 내용 | 최종 실측·판정 | 원본 증거 |
|---|---|---|---|
| 1. URL·메뉴 lifecycle | 공장↔관제, 직접 접근·새로고침·뒤로가기·반복 이동, 중복 renderer/polling 정리 | **PENDING** | 실제 브라우저 QA·캡처 |
| 2. 제품 수·실제 이동 | 8개 이상 고유 제품 ID와 Gazebo-origin 양의 이송 | 예비 10개 표본 확인; 최종 **PENDING** | API 예비 표본 + 최종 observer |
| 3. 30분 안정성 | 1,800초 벽시계, 가동·정지·재시작 반복, 겹침·낙하·소실·중복 검사·reset 기록 | **PENDING** | `stability-30min.json` 등 최종 원본 |
| 4. 장면·선택·작은 화면 | 1920×1080 흐름·롤러·단계, 선택 설비 실측값, 작은 화면 영상·접이식 정보 | **PENDING** | desktop/mobile 캡처 |
| 5a. 브라우저 renderer | 기본 환경 30FPS | **PENDING** | 실제 browser diagnostics |
| 5b. world 전송·표시 | world 10Hz 이상, 정상 관측-to-display p95 ≤500ms | **PENDING** | 물리 UTC origin + browser diagnostics |
| 5c. 실제 영상·대체 경로 | camera 실측 FPS·나이, CPU 소프트웨어 렌더·WebGL 대체 확인 | **PENDING** | camera metadata·CPU/영상 측정 |
| 6. stale·재연결·새 epoch | 마지막 pose freeze, 지연 표시, 최신 snapshot 복귀, 이전 run 상태 제거 | source 회귀 확인; runtime **PENDING** | 장애·복구 브라우저/API 원본 |
| 7. 인터락·수동 복구 | 실제 롤러 정지·제품 잔류 이동, 위험/지연 reset 거절, 회복 후 수동 재시작 | 코드 경로 확인; runtime **PENDING** | `controls.json` 등 최종 원본 |
| 8. 제품·AI·배출 증거 | ID·camera·HI·YOLO·CAM·모델·outfeed 일치, unknown 유지·중복 없음 | source 경로 확인; DB/runtime **PENDING** | backend oracle + 실제 run DB audit |
| 9a. 새 조건 PdM | F1≥0.80, CPU PdM≤100ms; 운전 조건·고장별 범위 표시 | **PENDING** | 새 조건/기준 회귀 및 동시 latency |
| 9b. 새 장면 YOLO | whole-frame mAP50≥0.80와 별도 제품 ROI mAP50·클래스별 AP·정상 오검출 | **PENDING** | `new-world-vision.json`·고정 first100 |
| 9c. CPU detector | 100장 평균≥20FPS; 동시 실행과 단독 benchmark 분리 | **PENDING** | latency CSV·workload 명시 report |
| 10. 회귀·새 clone | 원본 연결 회귀, 새 state/physics/UI 검사, 빈 data·새 DB·ONNX 없음의 한 Compose 기동 | **PENDING** | JUnit·독립 clone 기동 원본 |

안정성 observer는 API 표본에 기반한 좌표 bounds·겹침 heuristic을 사용한다. 영상 전체의 모든 접촉·충돌을 완전히 감사한 검사는 아니다. 도구의 `passed` 외에 10개 제품 보존·실제 logical ID 재공급·배출·DB 검사 건수와 stop/recovery 구간을 독립 확인한다.

world 전송 Hz·API 수신 Hz·display FPS와 camera FPS는 별도로 표기한다. source UTC가 물리 스레드에서 생성되므로 관측-to-display는 bridge 이후 구간에만 한정되지 않는다. browser 측정에는 브라우저와 컨테이너가 같은 호스트 시계를 쓰는 조건을 기록한다. 전체 CPU 부하의 평균·p95·측정 간격·서비스 범위도 **PENDING**이다.

## 새 장면 detector 평가 기록

평가는 학습·export·threshold fitting·가중치 승격 없이 고정 checkpoint로 수행한다. 지정한 균형 run의 첫 100개 고유 frame을 manifest 순서로 선택하며, 모델·이미지 hash와 실제 AI event join을 확인한다. 생성 class 참값은 private ROS reference와 평가 파일 안에서만 사용한다.

| 평가 항목 | 최종 값 |
|---|---|
| 정상/스크래치/찍힘/이물질 표본 수 | **PENDING** |
| 전체 영상 PyTorch 공식 mAP50 / mAP50–95 | **PENDING** |
| 전체 영상 ONNX mAP50 / 정상 오검출 | **PENDING** |
| 제품 ROI ONNX mAP50 / 클래스별 AP50 | **PENDING** |
| 제품 ROI 운영 threshold precision·recall·F1 / 정상 오검출 | **PENDING** |
| 동시 실행 ONNX·PyTorch 100장 평균 ms / FPS / p95 ms | **PENDING** |
| 단독 detector 같은 100장·설정의 평균 ms / FPS / p95 ms | **PENDING** |
| pose bracket 최대 sim 간격 / camera 수신 나이 / sensor 시각 차이 | **PENDING** |
| PUBACK 최대 delay / actual frame·model binding 확인 | **PENDING** |

전체 영상과 제품 ROI는 서로 다른 참값 범위다. ROI는 제품의 관측 기하로 정하며 결함 참값으로 잘라내지 않는다. 주변 support 구조·인접 제품에 대한 raw detector 출력은 보존한다. raw whole-frame 목표가 미달하면 ROI 통과로 대체해서 전체 영상 목표도 달성했다고 쓰지 않는다.

pose 라벨은 양쪽 관측 사이의 근사 보간이다. `pose_sim_time-camera_sim_time=0`은 보간을 camera 시각에서 평가했다는 뜻이며 원시 render-time pose의 정확도를 뜻하지 않는다. 프레임별 `pose_alignment.approximate=true`, gap·sequence·외삽 없음과 실제 bound를 함께 공개한다. JPEG hash와 AI 입력 bytes의 일치는 이 근사 라벨의 픽셀 정확도를 별도로 보장하지 않는다.

## 첫 버전의 한계와 후속 범위

- 한 라인·한 plate 형상·세 가지 단순 결함·정지 검사·고정 검사 카메라 조건이다. 균형 합성 표본은 실공장 정확도나 운영 불량률 추정 근거가 아니다.
- 현재 v1 진단 모델의 지원 조건은 약 955RPM의 합성 신호다. RPM·하중 변화, 약한 고장, 실제 베어링 RUL·오차 구간을 새로 검증하거나 개선한 결과는 아직 포함하지 않는다.
- 횡분류는 실제 body force actuator이고, 기계식 pusher 접촉이나 전체 공급·회송 동역학은 단순화되어 있다. 투입점 reset은 명시적 lifecycle 처리다.
- quality가 없는 제품은 중앙의 미분류 배출 `unknown`이며 정상·reject의 실제 분류로 집계하지 않는다.
- 중요한 발행의 PUBACK은 broker 인수 확인이다. bridge 메모리 batch는 프로세스 crash 후 복구하는 영속 outbox가 아니다. AI·storage queue 포화·장기 DB 장애를 포함한 모든 장애의 무손실 보장을 주장하지 않는다.
- `/analysis`, `/models`, 사건 replay, 여러 생산라인, 연속 이동·가림·반사·저대비 영상 검사, 운전 조건별 PdM 개선, 모델 승격, Gazebo 후속 버전 이전은 후속 단계다.

최종 결론: **PENDING — 위 원본 확인과 실제 미달·미검증 조건을 기록한 뒤 작성.**
