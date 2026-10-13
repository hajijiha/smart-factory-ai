# 공장 웹 독립 검토

작성 시각: 2026년 10월 13일 12:24 한국 시각. 상태: **최종 운전 전 identity·분류 검증**. [기계 판독 원본](independent-review.json)은 읽기 전용 SQL와 실제 파일 hash 결과를 포함한다.

이번 검토는 source audit와 현재 run의 제품 증거를 확인했다. **30분 연속 물리 안정성·실제 브라우저 성능·새 장면 정확도·새 clone 기동을 독립 측정한 보고서는 아니다.** 현재 run의 경과 시간이나 DB 누적 이력을 1,800초의 기록된 물리 관측으로 대체하지 않는다.

## 읽기 전용 DB·파일 검산

대상 `factory-1791828868974374`, PostgreSQL snapshot `2026-10-13T03:24:19.881527+00:00`. `REPEATABLE READ READ ONLY` transaction에서 SELECT만 수행하고 ROLLBACK했다. `data/factory-v1-evaluation/manifest.jsonl`, 검사 JPEG와 CAM 파일은 읽기만 했다.

| 독립 확인 항목 | 결과 |
|---|---:|
| product history / 고유 논리 제품 | 36 / 36 |
| 실제 quality event / 검사 센서 / private frame reference | 35 / 35 / 35 |
| 논리 제품에 여러 inspection ID | 0 |
| manifest product·inspection·event 중복 | 각각 0 |
| quality와 run·product·inspection·sensor event·model 불일치 | 0 |
| camera timestamp·sim_time·ROI·근사 alignment·frame SHA 불일치 | 0 |
| DB 이미지 bytes·실제 JPEG·AI 입력 SHA 불일치 | 0 |
| 동일 event의 HI·legacy quality 연결 불일치 | 0 |
| 실제 불량의 CAM artifact·path·metadata 연결 | 26개, 누락 0 |
| 실제 배출 기록 | 34 |
| 정상 lane / reject lane | 9 / 25 |
| 알려진 YOLO 판정·분류 명령·배출 y 좌표·lane 불일치 | 0 |
| unknown / unconfirmed 배출 | 0 / 0 |
| captured 또는 outfeed 상태의 quality 누락 | 0 |
| 진행 중 entered 제품의 quality 대기 | 1, outcome=unknown 유지 |

quality 35건은 정상 9·불량 26이며 34건이 배출됐다. 배출은 실제 `x≥2.05` 관측, 정상 `y<−0.30`, reject `y>0.30`와 저장된 분류 결과를 비교했다. 실제 수신한 quality 결과와 모순되는 물리 lane은 없었다. 미검사 entered 제품 1건을 정상으로 변환하지 않았다.

같은 품질 event의 sensor·health·quality·CAM을 JOIN하고, 저장된 모델 ID가 동결 기준 `vision.pt` SHA256 `de2b27a91410cd0ee12167a43b9651a9ef06198e6c86d80fdd2e9d84dffcde57`인지 확인했다. 여러 box의 quality rows를 중복 검사로 세지 않고 event 단위로 비교했다.

파일 취득은 DB snapshot 이후에 읽으므로 snapshot 뒤 ACK된 reference는 제외했다. 새 cross-worker JOIN이 아직 도착할 수 있는 5초 maturity 구간은 pending으로 집계하도록 사전에 정했다. 이번 snapshot의 해당 pending JOIN은 0건이었다. 이는 현재 진행 중 제품의 명시적인 quality 대기와 별개다.

## 발견한 문제와 수정 확인

1. MQTT 발행 성공을 확인하지 않고 검사를 완료하던 경로: sensor/image의 immutable batch를 개별 PUBACK 뒤에만 완료하며 lifecycle도 ACK 뒤에만 큐에서 제거하도록 바뀌었다. 연결·전달 실패는 직접 ROS 정지와 fresh manual resume latch를 사용한다. source에서 수정 경로를 확인했다. 브로커 장애 주입은 운영 담당자가 수행했으며 이 검토가 직접 실행한 것은 아니다.
2. camera와 별도 최신 pose를 무제한 결합하던 경로: 같은 실행·제품·검사의 관측 bracket 안에서만 보간하며 간격 ≤0.10 sim 초, 수신 나이 ≤2초, 센서 시각 차이 ≤0.15 sim 초를 요구한다. 이번 35개 실제 frame의 metadata·AI·private reference 연결도 확인했다.
3. 독립 outage observer의 ROS 토픽 오기: `/factory/world/state`로 수정된 source를 재확인했다.
4. 공장 화면의 camera selection freshness: 설비별 `freshness_seconds`를 사용하도록 수정돼 관제의 camera feed 2초 기준과 맞는다. 현재 영상 연결 상태와 마지막 AI 판정·경과 시간이 별개로 표시된다.

현재 읽은 범위에서 남은 중대한 source 오류는 발견하지 못했다. 이 판정은 모든 runtime 시나리오의 통과를 뜻하지 않는다.

추가된 controller watchdog도 읽기 전용으로 검토했다. simulator 전체가 pause되어 bridge가 동작하지 않아도 독립 controller의 250ms timer가 accepted live world의 지연을 감시하고 retained stop을 한 번 latch한다. handler와 timer는 같은 RLock을 사용하며 새 관측만으로 가동을 재개하지 않는다. 초기 world 미수신에는 arm하지 않고, 명시적인 dataset 모드는 과거 retained live state가 있어도 watchdog과 reset 양쪽에서 제외한다. 무입력 timer·회복 후 no-resume·retained-world dataset reset의 의미 있는 regression source를 확인했다. 실제 15초 pause 증거는 운영 담당자의 runtime 결과로 별도 검증한다.

## source와 평가 범위

- Gazebo 제품은 접촉·마찰로 이송되고 분류는 실제 body force를 사용한다. outfeed 이후 공급 reset은 새 logical serial을 부여하는 명시적 재배치다.
- 공유 layout은 outfeed roller의 handoff gate를 설명한다. 표시된 receiving tray로 제품을 완전히 넘기는 물리 bin transfer는 검증 범위에 포함하지 않는다. unknown quality의 중앙 배출도 정상·reject 분류로 세지 않는다.
- 공개 world·layout·AI에는 생성 class 참값을 넣지 않는다. 제품 판정 색은 실제 quality identity로 연결한다. raw whole-frame 검출과 관측 제품 ROI 결과는 별도로 남긴다.
- 새 vision evaluator는 고정 first-100 selection과 실제 camera·SHA·model binding, 근사 pose bound를 검증한다. timing-only 실행은 같은 sample/model hash를 고정하고 기존 정확도를 다시 산출하거나 덮어쓰지 않는다.
- live PdM evaluator는 waveform·sampling rate·관측 RPM만 inference에 전달하고 fault level은 scoring에 사용한다. 사전 정의된 settling 두 창과 약한 level 1을 명시하며 재학습·승격하지 않는다.
- source는 ROS2 Humble의 이미지·rclpy·gazebo_ros::Node를 사용한다. 최종 이미지·플러그인·source binding과 새 clone 기동은 별도 실행 원본에서 확인해야 한다.

관측 보간 라벨은 `approximate=true`다. camera 시각에서 평가한 보간 pose의 timestamp가 같은 사실을 원시 render-time pose 또는 수동 픽셀 mask 정확도의 증명으로 해석하지 않는다.

## 아직 독립 실행하지 않은 항목

운영 담당자가 전달한 최신 회귀는 227 passed / 2 예상 DATA skip, 원본 연결의 refinement 전 검사는 219 passed / 0 skip, frontend targeted 검사는 66 passed였다. 이 검토는 해당 테스트를 다시 실행하지 않았으며 최종 JUnit·기동·browser·장애 결과 원본을 별도 확인해야 한다.

watchdog 추가 후 원본 DATA 연결 전체 회귀는 운영 담당자 보고로 239 passed / 0 skip이었다. 이어진 retained-world dataset regression 포함 controller targeted 결과는 42 passed였다. 이 수치들은 독립 재실행 수치와 구분한다. 최종 30분 target은 `factory-1791862766610240`이며, 위 DB snapshot은 여전히 이전 run의 최종 운전 전 증거다.

이번 검토는 제어 명령, 고장 주입, 서비스 stop/start, schema 변경, DB write, 학습 또는 모델 교체를 수행하지 않았다. 첫 일반 Docker 연결은 sandbox named-pipe 권한으로 차단됐고 SQL을 실행하지 못했다. 이후 승인된 직접 Windows Docker 읽기 경로가 정상 동작하여 위 검산을 수행했다. WSL 우회 경로는 사용하지 않았다.

최종 30분 run이 새로 실행되면 같은 읽기 전용 audit를 새 run ID로 재실행하고 이 두 파일을 갱신해야 한다.

