# 공장 시뮬레이션·통합 웹 첫 버전 결과

작성일: 2026년 10월 13일, 한국 시각. 구현 `f38912f`, 검증한 수정 소스 `9c4b793f7be7945b70a07d1d7cc63e4511aa1edc` (tree `14cfcae5271cf02df5f84406869d976b14cac3cd`). 이후 문서 커밋은 이 소스의 결과와 증거를 게시한다.

계획의 첫 배포 범위인 단계 0–6을 구현하고 실제 실행·정지·장애 복구·새 장면 평가를 확인했다. **10개 제품의 물리 이송과 웹 연동, 안전 수동 복구, 새 장면 AI 목표는 통과했다.** 전경 영상의 간헐적 지연과 실시간 배율 목표 미달은 아래에 공개한다. 후속 분석 페이지·사건 재생·다중 라인·운전 조건별 모델 개선까지 완료했다는 의미는 아니다.

운영·재현은 [factory-live.md](../../factory-live.md), 독립 검토는 [independent-review-final.md](independent-review-final.md), 실행 복구 상태는 [runtime-final.json](runtime-final.json)에 기록한다.

## 구현한 결과

| 범위 | 실제 동작 |
|---|---|
| 공통 웹앱 | `/`·`/factory` 공장, `/monitor` 관제, 메뉴·직접 URL·새로고침·브라우저 이력 지원 |
| 물리 생산라인 | 72개 ODE 롤러와 10개 dynamic plate의 접촉·마찰 이송, 검사 정지, 판정에 따른 횡방향 힘, 실측 배출 |
| 공장 장면 | 모터 하우징·검사셀·제어반·경광등·펜스·랙·바닥 통로와 롤러 회전 표식 |
| 웹 3D와 영상 | 공통 layout, 실제 관측 pose·관절 속도, 설비·제품 선택, 실제 Gazebo 전경/검사 JPEG |
| 제품 증거 | run/product/inspection/sensor/camera/model ID에 HI·YOLO·CAM·실측 outfeed를 연결 |
| 안전 복구 | 위험·운영자·world/MQTT 지연 정지, 독립 watchdog, 위험/오래된 관측 재시작 거절, 회복 후 수동 재시작 |
| 기준 보존 | 모델 네 파일과 원본 데이터 보존, additive DB schema, 이번 확장의 재학습·강화학습·모델 교체 없음 |

배출 후 공급은 빈 투입점에서 명시적으로 pose를 재설정하고 새 제품 ID를 부여한다. 물리 회송 라인·수거함 낙하는 구현하지 않았다. 미검사 제품은 `unknown`으로 유지하며 정상 판정으로 집계하지 않는다. 진동은 Gazebo 관절 관측에 수식을 적용한 합성 파형이다.

## 실행 조건과 재현성

| 항목 | 실제 조건 |
|---|---|
| 호스트 | Intel Core Ultra 5 225H, 14 논리 코어, Windows 11 Home, 균형 조정 전원 구성 [hardware-final.json](hardware-final.json) |
| 실행 | WSL Ubuntu, Compose `smart-factory-web-20261013`, 9개 서비스와 실제 IAB 공장 화면 |
| 평가 run | `factory-1791898116588790`, `balanced-evaluation`, 10개 제품 |
| 런타임 | Python 3.10.12, PyTorch 2.5.1+cpu, ONNX Runtime 1.20.1, Gazebo 11.10.2, ROS2 Humble |
| 물리 조건 | step 0.004초, solver 30회, 목표 realtime factor 1, 모터 100rad/s ≈ 954.93RPM, 롤러 setpoint 6rad/s |
| 자원 제한 | 컨테이너 cpu.max `max 100000`, memory.max `max`; WSL/호스트 가용 자원과 구분 |
| 웹 측정 | 1920×1080 viewport override (실제 content 1905×1080), mobile 390×844 (content 375폭), 실제 WebGL2 |
| 가속·시계 | 서버 AI·Gazebo는 CPU/Mesa; 브라우저 GPU vendor·가속 방식/버전 별도 측정하지 않음. 표시 지연은 같은 호스트 UTC 기준 |

CPU governor는 채집하지 않았다. 기존 8084 및 독립 clone 8087 서비스는 종료하고 볼륨을 보존했다. 전체 실행 측정과 단독 detector 측정을 구분하며, 기존 v1의 성능을 새 장면의 결과로 재사용하지 않는다.

## 계획 기준과 검증 결과

| 기준 | 결과 | 원본 |
|---|---|---|
| URL·화면 lifecycle | 메뉴·직접 접근·새로고침·뒤로/앞으로·반복 이동, mobile 두 페이지 overflow 0 | [browser-ui-final.json](browser-ui-final.json) |
| 다중 제품 이송 | 10개 body 유지, 288개 logical 제품 ID, 278회 배출 후 재공급 | [stability-30min.json](stability-30min.json) |
| 30분 운전 | 벽시계 1,800.040초, 18,897개 world 관측, 좌표 bounds·겹침·소실 heuristic finding 0 | 같은 원본 |
| 장면·선택·영상 | 전체 공장과 제품 흐름, 선택 정보, 실제 두 카메라, mobile 접이식 정보 | [factory-desktop-final.png](factory-desktop-final.png), [factory-mobile-final.png](factory-mobile-final.png), [monitor-mobile-final.png](monitor-mobile-final.png) |
| 30FPS 목표 | 실제 브라우저 두 표본 약 144FPS | [browser-performance-final.json](browser-performance-final.json) |
| 10Hz·표시 p95≤500ms | source 10.683Hz, HTTP 고유 관측 10.498Hz, browser 관측→표시 p95 174.95/168.72ms | browser 원본과 stability |
| 실제 카메라 | 검사 1.763FPS / 영상 나이 p95 560.4ms, 전경 0.751FPS / p95 4,054.1ms | stability `camera_observations` |
| stale freeze·복구 | broker 단절 중 실제 화면의 seq/제품 pose 동일, 재시작 잠금; API watchdog freeze·새 관측 복귀·수동 복구 | [browser-freeze-final.json](browser-freeze-final.json), [controller-watchdog-final.json](controller-watchdog-final.json) |
| 인터락 | 운영자 정지·위험 reset 거절·정상 회복 후 정지 유지·수동 재시작 5개 검사; broker 7개·watchdog 7개 검사 통과 | [controls-final.json](controls-final.json), [broker-disconnect-final.json](broker-disconnect-final.json) |
| ID·AI·배출 | SQL snapshot 458개 검사 연결, 중복/혼입 0; 456개 실측 배출과 판정·명령·좌표 일치 | 독립 검토 |
| PdM F1≥0.80·≤100ms | 관리도/AE F1 각각 1.000, 평균 1.895ms, p95 2.928ms | [live-pdm-final.json](live-pdm-final.json) |
| whole-frame mAP50≥0.80 | 고정 첫 100장 공식 PT mAP50 0.862806, mAP50–95 0.696098 | [new-world-vision.json](new-world-vision.json) |
| CPU detector ≥20FPS | 전체 서비스 가동 ONNX 평균 38.466ms = 25.997FPS, p95 65.150ms | [new-world-latency.csv](new-world-latency.csv) |
| 회귀 | 원본 DATA 읽기 전용 연결 273 passed, 0 skipped, 0 failures | [tests-final.xml](tests-final.xml) |
| 새 clone 한 번 기동 | 정확한 소스 clone·빈 data·새 DB/MQTT 볼륨·ONNX 미생성에서 Compose up --build, 9서비스·실제 영상·AI join 확인; 271 passed / 원본 부재 2 예상 skip | [clean-startup.json](clean-startup.json) |
| 카메라 재시작 수정 | Xvfb 동적 displayfd, 준비/PID/10초 deadline 확인; 두 차례 일반 restart 후 두 카메라 복귀 | [display-restart-proof.json](display-restart-proof.json) |
| WebGL 대체 | production JS의 WebGL2 없음·context 생성 예외·stale 영상 분기 확인; 실제 브라우저 GPU 실패/복구는 미시험 | [browser-fallback-final.json](browser-fallback-final.json) |

30분 관측의 world stale는 34,901표본 중 263회다. 계획된 15초 simulator pause와 2초 guard에서 예상되는 약 258–266회와 일치하지만, 집계 원본에 각 poll 시각이 없어 전부 그 구간에 귀속됐다고 확정하지 않는다. 정지 표본 2,868개는 운영자/PdM/검사/장애 시험을 포함한다. browser freeze 원본은 **30분 운전 이후 broker 단절 중 실제 화면**의 증거이며, 별도 watchdog pause 당시 UI 채집을 대신하지 않는다.

안정성 도구는 API 좌표 표본의 heuristic 검사다. 모든 순간의 접촉·충돌을 완전히 감사한 영상 증거가 아니다. 양의 X 변위 누적도 실제 이동의 표본 지표이며 경로 총거리와 다르다. SQL snapshot의 나머지 2개 제품은 검사 완료 후 이송 중이었다.

API까지의 source 나이 p95 103.9ms와 브라우저 표시 p95는 다른 측정이다. world source Hz·renderer FPS·카메라 FPS·detector FPS도 각각 다른 값이다.

## 새 장면 YOLO와 CPU 속도

첫 100개 고유 검사 순서를 고정했다. 선택 manifest SHA256은 `ae80ca9cc3eaf9644ed6362bb7632b6e2bd841777bcdf1d8de6a05a8e8203bb0`이다. 참값은 평가 전용이고 배포 AI 입력이나 ROI 정의에 노출하지 않았다. [selection](new-world-selection.jsonl), [predictions](new-world-predictions.jsonl), [평가 그림](new-world-vision.png)을 보존했다.

| 평가 범위 | mAP50 | mAP50–95 | 해석 |
|---|---:|---:|---|
| 전체 영상 PT 공식 Ultralytics | 0.862806 | 0.696098 | 목표 0.80 판정에 사용 |
| 전체 영상 ONNX 독립 matching | 0.949681 | 0.725238 | 공식 PT와 matching 절차가 달라 직접적인 export 정확도 향상 근거가 아님 |
| 관측 pose 기반 제품 ROI PT/ONNX | 0.995000 | 0.826201 | 동일 평가 절차에서 두 결과 일치 |

ROI 클래스별 AP50은 scratch/dent/contamination 각각 0.995다. AP50–95는 0.700872/0.895500/0.882232다. 전체 영상 PT AP50은 각각 0.919524/0.823350/0.845545다.

제품별 정상/스크래치/찍힘/이물질을 각각 25개 취득했고, 전체 영상에는 주변 제품까지 합쳐 147개 결함 label이 있다. 정상 제품을 대상으로 한 25개 영상 중 24개는 이웃 제품의 결함이 보인다. 그래서 **제품 정상 오검출 0/25**와 **전체 영상 무결함 표본 1개에 대한 오검출 1/1**을 섞지 않는다. raw whole-frame 운영점의 box precision/recall/F1은 0.572/0.918/0.705이며, 제품 ROI 운영점의 TP75/TN25/FP0/FN0, F1 1.000과 별도다. 정상 25개에서 오검출 0이라는 결과의 Wilson 95% 상한은 약 13.3%다.

| 부하 조건 | ONNX 평균 / FPS / p95 | PT 평균 / FPS / p95 |
|---|---|---|
| 9서비스·공장 브라우저 동시 | 38.466ms / 25.997 / 65.150ms | 164.249ms / 6.088 / 540.640ms |
| 단독·같은 첫100장 | 10.169ms / 98.335 / 16.717ms | 23.988ms / 41.688 / 40.937ms |

단독 원본은 [new-world-timing.json](standalone-20261013-final/new-world-timing.json)에 있다. 7개 실행 서비스를 정지하고 broker/database만 idle 상태로 유지했으며 공장 브라우저도 about:blank로 이동했다. network none인 별도 컨테이너에서 4threads·batch1·warmup10으로 이미지와 두 모델 hash를 다시 대조했다. 정확도는 반복하지 않았다. 시간 범위는 resize·BGR/RGB·정규화·추론·NMS이며 JPEG decode·제품 ROI filter·CAM·MQTT·DB·브라우저 표시를 제외한다. 현재 배포 detector는 ONNX이며 전체 서비스 동시 실행에서 PT가 20FPS를 달성했다고 주장하지 않는다.

선택 100장 모두 실제 AI 입력 JPEG·model·제품·카메라·센서·HI join과 ONNX export binding을 확인했다. pose는 camera 시각을 양쪽 관측 사이에서 근사 보간하며 외삽하지 않는다. 최대 bracket 0.092sim초, 영상 수신 나이 최대 0.1183초, sensor offset 절대값 최대 0.116sim초, PUBACK delay 최대 0.0551초다. JPEG byte 일치는 label projection의 픽셀 정확도를 보장하지 않는다.

## PdM·자원·남은 성능 제약

PdM은 고정 955RPM의 합성 파형에서 fault 0/1/3/5별 25창, 사전 지정한 settling 2창 제외 후 첫 fresh join 100개를 사용했다. 관리도와 AE 모두 TP75/TN25/FP0/FN0, 배포 HI 차이 0이었다. FFT·특징·두 모델·HI의 4threads CPU 시간이 위 표의 1.895ms다. **9서비스·카메라·웹은 가동했지만 이 PdM 측정 구간의 컨베이어는 의도적으로 정지했고 진단 모터는 계속 회전했다.** 일반 이송 중 PdM latency 또는 실제 베어링 성능으로 확대하지 않는다. 원시 파형은 [observations gzip](live-pdm-final-observations.jsonl.gz)에 보존한다.

자원 원본은 [resources-30min.json](resources-30min.json), 계산 요약은 [resource-summary-final.json](resource-summary-final.json)이다. Docker CPU 100%는 논리 코어 1개이며 호스트 전체 100%가 아니다. 9서비스 10초 간격 180표본에서 각 표본의 서비스 CPU 합계를 계산했다. 합계 평균 293.36%(2.934코어), nearest-rank p95 1156.48%, 최대 1543.91%이며 높은 tail도 원본 그대로 공개한다. p95는 정렬 표본의 ceil(180×0.95)번째 값이며 선형 보간 p95 1157.32%와 구분한다. 이 합계를 정확한 호스트 전체 이용률로 해석하지 않는다. 합산 메모리는 1.859–2.496GiB다.

- 실측 realtime factor는 **0.84945**로 목표 1에 미달했다. 중간의 계획 pause와 시험 부하를 포함한 30분 벽시계 대비 값이다.
- 전경 카메라는 2초 freshness 기준을 자주 넘었다. 단일 9서비스 30초 추가 trace에서도 최대 frame 간격 4.30초가 남았다. world stale·cache reject 증가가 없고 broker 전달 자체는 수 ms였으므로 상류 카메라 도착 지연으로 좁혔지만 renderer/DDS의 구체 원인은 확정하지 못했다. [추가 trace](overview-single-stack-summary.json)
- 검사 카메라는 같은 추가 trace에서 stale 0, 약 1.86FPS였다. 카메라의 낮은 실제 FPS를 3D 화면 144FPS로 대체해서 보고하지 않는다.
- WebGL2 unavailable fallback 코드 분기를 확인했으나 실제 GPU context loss·복구/완전한 GPU 미탑재 브라우저 조합의 성능은 미시험이다.

## 수정·기준 보존·후속 범위

벽시계의 수십 ms 역방향 보정이 수신 완료한 관측을 future로 오판해 정지시키던 문제를 수정했다. 수신 당시 source delay와 monotonic 경과시간으로 cache의 나이를 계산한다. 실제 future-at-receipt·2초 world/camera·5초 health/line 한계는 유지한다. controller의 일부 reset 경로에는 legacy UTC freshness 확인이 남아 있으므로 모든 reset 경로가 시계 보정에 면역이라고 주장하지 않는다. 이 경로는 보수적으로 재시작을 거절하며 자동 재시작하지 않는다.

검사 발행은 immutable sensor/image batch의 개별 PUBACK 뒤에 완료하고 미확인 부분만 같은 UUID·bytes로 재시도한다. PUBACK은 broker 인수 확인이며 AI·DB commit 완료 확인은 아니다. outbox는 bridge 메모리 범위이며 프로세스 crash·DB 장기 장애·모든 queue 포화의 무손실 복구는 포함하지 않는다.

[baseline.json](baseline.json)의 네 SHA256을 그대로 유지했다.

| 기준 파일 | SHA256 |
|---|---|
| vision.pt | `de2b27a91410cd0ee12167a43b9651a9ef06198e6c86d80fdd2e9d84dffcde57` |
| pdm.pt | `f829b824123eee1adc66b2fd6cf4fb9a8c50ffd2ba8ea343c06730e93500bb73` |
| rul.pt | `602def1bf05519682fb97a0c7f41507d141aed920bd63f616af6a87a5b2ef323` |
| drift-reference.json | `8bc12684ec570284af367730e68b82d8a1b03ba922f5dbf46daaad5095eae45d` |

소스 작업과 공개 기본 branch는 `main`이다. 기존 고유 커밋은 `archive/completion-20261013`, `archive/data-validation-20261013`, `archive/process-control-20261013`, `archive/pre-completion-20261013` 태그로 보존했다. 새 branch 이름에 codex를 사용하지 않았다.

원본 DATA 연결 회귀 273개는 실패·skip 0이며 기존 라이브러리 deprecation warning 15개를 기록했다. 새 clone은 **로컬 Git의 검증 소스를 새로 복제한 증거**이며 GitHub에서 내려받은 공개 clone 검증과 구분한다. 공개 증거 도구까지 포함한 [quality-final.json](quality-final.json)의 Python 정의 621개 docstring 누락·Conventional Commit 위반은 0이다.

이번 범위는 한 라인·한 판형 제품·단순 세 결함·정지 검사·합성 진동이다. `/analysis`, `/models`, 사건 재생, RPM·하중·시동 강건성/약한 고장, 이동·가림·반사·저대비 검사, 실제 RUL 교정·오차 구간, 다중 라인, 영속 outbox와 Gazebo 후속 버전 이전은 후속 단계다.
