# 공장 웹 v1 최종 독립 검토

검증 소스: `9c4b793f7be7945b70a07d1d7cc63e4511aa1edc`, tree `14cfcae5271cf02df5f84406869d976b14cac3cd`.
최종 run: `factory-1791898116588790`.
DB snapshot: **2026-10-13 14:35:44.731177 UTC / 23:35:44.731177 한국 시각**.

검토 범위에서 미해결 중대한 source 오류나 제품·검사·영상·배출 연결 모순을 발견하지 못했다. 읽기 전용 SQL·파일 SHA·고정 표본·성능 산술을 독립 검산했다. 원본 runtime·브라우저·기동 도구는 운영 담당자가 실행했으며 이 검토가 다시 제어하거나 추론한 결과와 구분한다. 기존 `independent-review.json/md`는 보존했다.

## 누적 제품·검사·물리 배출

`REPEATABLE READ READ ONLY` transaction에서 SELECT만 수행하고 ROLLBACK했다. 실제 DB 이미지 bytes·JPEG·private reference·AI 입력 SHA와 동일 event의 sensor·HI·quality·CAM을 비교했다.

| 확인 항목 | 결과 |
|---|---:|
| product_history / 고유 논리 제품 | 458 / 458 |
| quality event / 검사 sensor / private reference | 각각 458 |
| 논리 제품의 복수 inspection / manifest ID 중복 | 0 / 0 |
| run·product·inspection·event·camera·HI·model·JPEG 혼입 | 0 |
| 불량 CAM artifact·path·metadata 연결 | 343, 누락 0 |
| 실제 outfeed | 456 |
| 정상 lane / reject lane | 114 / 342 |
| actual quality·분류 명령·배출 좌표·lane 모순 | 0 |
| unknown / unconfirmed outfeed | 0 / 0 |
| captured 또는 outfeed의 quality·health 누락 | 0 |
| snapshot 현재 이송 중 | captured 2개 |

배출은 실제 `x≥2.05`, 정상 `y<−0.30`, reject `y>0.30` 관측과 비교했다. 미분류 중앙 배출은 정상·reject로 간주하지 않는 정책을 source와 카운터로 확인했다. 이번 snapshot에는 unknown 배출이 없으므로 미분류 제품의 현장 동작을 이 누적 건수로 증명하지 않는다.

관측을 읽는 동안 생기는 cross-worker JOIN 차이를 위해 5초 maturity 구간을 사전에 두었다. 이번 pending JOIN과 snapshot 뒤 새 reference 제외 건수는 모두 0이다. **누적 snapshot 458개와 30분 observer의 288개는 시간 범위가 다르다.** receiving tray로의 완전한 bin transfer는 검산하지 않았고 outfeed roller handoff gate를 검산했다.

## 새 영상의 고정 100장과 독립 산술

선택 SHA: `ae80ca9cc3eaf9644ed6362bb7632b6e2bd841777bcdf1d8de6a05a8e8203bb0`.
검출 배열 SHA: `a8cab4ab3bb0e25b3073393a227cf9a40f71ae28de4515cc00f472ce27b40789`.

private manifest의 해당 run 첫 100개와 event 순서·원본 내용·manifest 행번호·JPEG/label SHA가 일치했다. 정상·scratch·dent·contamination은 각각 25개다. 실제 저장된 deployed raw/ROI 검출의 운영 threshold 산술을 다시 계산했고, 새 `new-world-predictions.jsonl`의 저신뢰도 배열로 confidence 순의 1:1 class/IoU matching과 101점 precision envelope 적분을 별도로 구현했다. 모델을 실행하지 않았다.

| 평가 범위·방법 | mAP50 | mAP50–95 |
|---|---:|---:|
| official PT whole-frame `model.val` 원본 | 0.86280628 | 0.69609826 |
| raw ONNX, 저장 배열의 독립 matching 재구성 | 0.94968137 | 0.72523767 |
| target ROI ONNX, 독립 재구성 | 0.99500000 | 0.82620134 |
| target ROI PT, 독립 재구성 | 0.99500000 | 0.82620134 |

**official PT whole-frame과 raw ONNX는 matching·평가 경로가 다르다.** official PT 개별 배열은 저장되지 않아 class AP50 평균만 검산했다. 두 whole-frame 숫자의 차이를 ONNX export에 따른 정확도 개선으로 해석하지 않는다.

실제 운영 threshold의 target ROI는 TP75/FP0/FN0/TN25, F1 1.0, 정상 FPR **0/25**였다. 정상 FPR의 Wilson 95% 상한은 **13.32%**이므로 실공장 무오검출을 주장하지 않는다. raw whole-frame은 TP99/FP1/FN0/TN0이며 정상 FPR **1/1**이다. raw detection IoU50는 TP135/FP101/FN12, F1 **0.70496084**다. 선택 제품이 정상이어도 주변 제품의 결함이 보일 수 있으므로 whole-frame 정상 분모를 선택 정상 제품 25개와 혼동하지 않는다.

100개 CSV의 event·제품·inspection·이미지/label SHA·PT/ONNX latency가 보고서 배열과 일치했다. 동시 실행 ONNX는 평균 **38.4662ms / 25.9969FPS**, p95 **65.1495ms**이고 PT는 **164.2491ms / 6.0883FPS**, p95 **540.6404ms**다. 범위는 resize·색 변환·정규화·inference·NMS이며 JPEG decode·DB·ROI filter·CAM은 제외한다. 단독 benchmark 원본도 이어서 독립 검산했다. 같은 100장 순서·이미지 SHA·PT/ONNX hash·4threads/batch1/warmup10으로 ONNX **10.1693ms / 98.3349FPS / p95 16.7171ms**, PT **23.9875ms / 41.6883FPS / p95 40.9372ms**다. 공장 브라우저는 about:blank, 7개 worker는 정지, broker/database는 idle인 network-none 별도 컨테이너였다. 원본 selection·accuracy·CSV·prediction hash와 source bound 파일이 유지됐고 정확도는 반복하지 않았다. `standalone-20261013-final`의 timing·CSV·runtime-context·copy verification은 **430항목**, finding0이다. 이 숫자는 동시 실행이나 최종 복구 상태의 처리량으로 사용하지 않는다.

## 새 PdM 100창

`live-pdm-final.json`과 `live-pdm-final-observations.jsonl.gz`는 같은 최종 run이다. 레벨 0·1·3·5가 각각 25창이며 약한 level1도 포함한다. 100개의 sensor/health DB event·run·timestamp·sim_time·RPM·sampling rate·fault label 및 health flag가 저장된 MQTT 관측과 일치했다. raw waveform은 설계상 sensor DB JSON에서 제외되므로 2048개 길이와 판정 산술은 보관된 observations gzip으로 검산했다.

chart·AE 모두 TP75/FP0/FN0/TN25, F1 **1.0**, 정상 FPR **0/25**였다. blind/deployed flag는 각각 **100/100**, HI 최대 차이는 **0**이다. `waveform·sampling_hz·measured RPM`만 inference에 전달하고 fault level은 scoring에만 쓰는 source 경로를 확인했다. 관측 배열·DB JOIN·운영 threshold·PdM 산술 검산은 **1,728항목**, 새 AP·CSV·모델 보존 검산은 **1,752항목**이며 finding은 모두 0이다.

PdM blind CPU 시간 배열도 다시 계산해 평균 **1.89547ms**, p95 **2.92845ms**가 원본과 일치했다. 100장 capture의 최대 bracket **0.092sim초**, 수신 나이 **0.118316초**, sensor 시차 **0.116sim초**, PUBACK delay **0.055059초**도 원본과 일치했다.

조건은 약 955RPM 합성 진동·한 load/noise 설정이며 진단 시험 중 conveyor는 의도적으로 정지했다. 이 F1과 latency를 가동 라인의 연속 처리량이나 실제 베어링 고장 정확도로 확대하지 않는다. level0 복원·안전 수동 reset 원본은 accepted=true다.

## 30분·화면·장애 원본의 범위

- `stability-30min.json`: 1,800.0398초, body 10개 유지, 288 논리 ID·278 재공급 reset, 양의 이송, integrity heuristic finding0. source **10.68254Hz**, API 관측 **10.49810Hz**, source→API 나이 p95 **103.9ms**, RTF **0.84945010**다. RTF1 달성이나 원시 물리 접촉 전수 검사를 뜻하지 않는다.
- HTTP 표본은 34,901개이고 world stale는 **263**이다. 운전 중 15초 simulator pause를 포함한다. 개별 polling 시각이 없어 모든 stale 표본을 pause로 확정 귀속하지 않는다. inspection **1.76329FPS / 나이 p95 560.4ms / stale266**, overview **0.75054FPS / 나이 p95 4,054.1ms / stale7,270**이다. 상부 영상의 간헐적 지연은 남은 한계다. 2초 freshness 기준을 완화하지 않았다.
- `resources-30min.json`: 1,800.0661초 동안 180개 표본마다 9서비스가 존재했다. Docker 100%=논리 CPU 한 개 기준의 합계 평균 **293.3637%**, p95 **1,157.3170%**(선형 보간), 최대 **1,543.91%**다. 합계 메모리는 **1.85936–2.49599GiB**다.
- `browser-performance-final.json`: 실제 1920×1080 override의 두 표본은 renderer **144.035 / 143.986FPS**, source→display p95 **174.947 / 168.720ms**이며 각 latency buffer는 180개다. source JS의 `floor(N×0.95)` order statistic을 재계산했다. 브라우저·컨테이너가 같은 호스트 UTC라는 전제이고 GPU vendor는 미측정이다. 화면 FPS와 world/camera Hz는 별개다.
- `browser-ui-final.json`: 실제 관제 이동·뒤로/앞으로·관제 reload·반복 메뉴·공장 직접 URL·설비 선택을 확인했다. 모바일 390×844 override에서 실제 content375px, 가로 overflow0, 접이식 상세와 실제 두 camera JPEG가 확인됐다. WebGL2는 동작했지만 GPU 장애 강제와 fallback 전환은 실행하지 않았다.
- `controls-final.json`의 정지·안전 수동 restart·위험 reset 거절·회복 후 정지 유지·명시 restart 5개가 true다. `controller-watchdog-final.json`은 15초 pause의 독립 retained stop·409·cached pose freeze·fresh 후 no-resume·명시 restart 7개가 true다. 이 pause의 실제 UI 채집을 수행했다고 쓰지 않는다.
- `broker-disconnect-final.json`의 30초 장애 7개가 true다. `browser-freeze-final.json`은 **30분 운전 이후 broker 장애** 때 실제 페이지의 stale/controls lock·sequence freeze·pose freeze 3개다. watchdog pause UI 증거로 재사용하지 않는다.

## source·기동·보존

immutable sensor/image batch는 개별 PUBACK 뒤에 완료하고 lifecycle도 ACK 뒤에 제거한다. bounded pose bracket≤0.10 sim초·camera 수신나이≤2초·sensor 시차≤0.15 sim초를 검증한다. frame 참조는 `approximate=true`인 관측 보간이며 원시 render-time pose나 수동 pixel mask가 아니다. Gazebo는 실제 collision/friction 롤러 이송과 transverse body force 분류를 사용하고, outfeed 이후 투입점 재배치는 새 논리 ID의 명시 lifecycle이다. 공개 world/API에 생성 class를 넣지 않는 source 경로를 확인했다.

controller는 handler/watchdog가 같은 RLock을 쓰며 accepted live world 이후에만 250ms watchdog을 arm하고 dataset 모드는 제외한다. fresh 관측만으로 latch를 풀지 않는다. validated source delay+monotonic receipt는 accepted world의 UTC 보정으로 인한 watchdog 오정지를 막는다. legacy `recent(health/line/reset message)`는 현재 UTC와 비교하므로 모든 manual reset guard가 UTC 보정에 무관해졌다고 주장하지 않는다. 일시적인 안전 복구 거절 가능성을 유지하며 자동 재개는 없다.

`tests-final.xml`은 273 passed/0skip/0failure/0error, `quality-final.json`은 공개 증거 도구를 포함한 621 definitions/위반0이다. `display-restart-proof.json`은 카메라 복구 1회+일반 simulator restart2회에서 실제 두 JPEG·새 epoch·own Xvfb PID/동적 display를 확인했다. `clean-startup.json`은 source9c의 빈 data·새 DB·ONNX 없음의 **로컬 committed clone**에서 한 Compose 기동·9서비스·자동 frozen PT export binding·3개 완전 join·271pass+원본 dataset 부재2skip을 확인했다. 공개 원격 fresh-download 검증과 장시간 성능 대체 증거는 아니다.

기존 vision.pt·pdm.pt·rul.pt·drift-reference.json 네 hash는 독립 읽기에서 기준과 일치했다. Windows checkout에는 runtime-generated vision.onnx가 없으므로 graph SHA·export binding은 live evaluator의 before/after 검사와 clean-startup 원본 범위로 구분했다. 재학습·threshold fitting·모델 승격은 수행하지 않았다.

상세 카운터·검산 결과·원본 경계는 `independent-review-final.json`에 보존했다. 이번 검토는 제어 명령·고장 주입·서비스 lifecycle·DB/schema write를 실행하지 않았고 source·REPORT·기존 독립 검토 파일을 수정하지 않았다.
