# 독립 검증 기록

Smart Factory AI 구현과 별도로 요구사항·소스·생성 데이터·실제 역전파를 검토했다.
기존 To Do List 프로젝트는 이 검증 범위에 포함하지 않았다. 아래 수치는 실제
검사 결과이며, 최종 모델 성능과 전체 시스템의 완료 여부는 해당 결과 파일에서
확인해야 한다. 기계가 읽을 수 있는 동일 범위의 기록은 `results/qa.json`이다.

## 격리 회귀 검사

2026년 10월 8일 CPU 전용 런타임 이미지에서 **27개 테스트가 모두 통과**했다.
최종 실행 시간은 7.09초였다. Matplotlib/Pyparsing의 deprecated API 경고 14건은
의존 패키지에서 발생했고 테스트 실패는 없었다.

네트워크가 없는 일회성 컨테이너에 프로젝트를 읽기 전용으로 마운트했다.
실행 중인 MQTT 브로커·DB·학습 작업에 테스트 메시지를 발행하거나 상태를
변경하지 않았다. 임시 first-start export 검사만 컨테이너의 임시 폴더를 썼다.

```bash
cd ~/smart-factory-ai
docker run --rm --network none --cpus=2 \
  -v "$PWD:/app:ro" -w /app \
  smart-factory-runtime:local \
  python3 -m pytest tests -q -p no:cacheprovider
```

| 검증 | 확인한 실패 조건 또는 근거 |
|---|---|
| FFT·베어링 주파수 | 알려진 사인파의 RMS·주파수·진폭, BPFO/BPFI 순서, Nyquist |
| 상태 점수 | 오류 증가에 따른 정상/위험, Score·HI 범위 |
| 인터록 | 위험 반복 메시지에서 정지 유지, 정상 회복만으로 자동 재시작하지 않음 |
| 재시작 안전성 | 오래된 HI, 낮은 HI, 고장 레벨, 미래 HI/라인 시각, 오래된 라인 관측 거절 |
| 시각 오류 | 없는 메시지·없는 timestamp·잘못된 날짜·시간대 없는 날짜에서 fail closed |
| 실제 시차 | 미검사 5초 구간을 제거해 시간축을 압축하지 않음 |
| 최초 Vision 실행 | `vision.pt`만 있는 상태에서 설정된 크기·CPU로 ONNX export를 수행 |
| ONNX 처리 | BGR→RGB, [0,1] 정규화, 좌표 복원, 동일 클래스 중복 억제·다른 클래스 유지 |
| 데이터 | 모든 JPEG 해시·경로·scene 중복 및 센서 특징/라벨/분할/출처 순서 검사 |
| SDF·모듈 실행 | 변경된 YAML FOV/size의 실제 XML 반영, 배포된 `-m simulator.world` 명령으로 임시 SDF 생성 |

추가 검사는 `tests/test_review_controller.py`, `test_review_analytics.py`,
`test_review_dataset.py`, `test_review_vision.py`, `test_review_world.py`에 있다. 생성 데이터가 없는
새 클론에서는 데이터 의존 검사 2개가 이유를 표시하며 skip된다. 데이터를
생성한 뒤 같은 명령을 실행하면 모든 데이터 검사가 포함된다.

## 발견한 결함과 수정 확인

1. **처음 실행 시 ONNX export 실패**: `vision.service`의 `CONFIG` import가
   빠져 있었다. 기존 ONNX 파일이 있는 환경에서는 숨어 있다가 weights-only
   배포에서 `NameError`가 발생했다. import 수정 후 최초 실행 회귀가 통과했다.
2. **미래 시각과 오래된 라인 상태로 재시작 허용**: 기존 검사는 HI의 age가
   5초 이하인지만 확인했고 음수 age와 라인 시각을 검사하지 않았다. 미래 HI
   60초, 오래된 fault-level 관측 60초 두 케이스를 실제 실패로 재현했다.
   HI와 라인 모두 `0 <= age <= 5` 및 올바른 timezone을 요구하도록 고친 뒤
   해당 회귀와 시각 오류 검사가 통과했다.
3. **스크래치 Grad-CAM 전체 0**: raw Conv2d의 actual gradient가 존재해도
   ReLU 결과는 모두 0이었다. 마지막 backbone Conv block의 BN/SiLU 이후
   출력에서 비교한 실제 근거는 다음 표와 같다.
4. **컨베이어 명령 손실 가능성**: ROS 콜백이 한 개 문자열 슬롯을 덮어썼으므로
   scene 명령이 정지 명령을 대체할 수 있었다. C++ FIFO와 scene의 running
   상태 반복 전달로 수정했다. 물리 관절의 실제 정지·재시작 결과는
   `results/integration.json`에서 별도로 확인한다.
5. **PdM benchmark의 회전 주파수 불일치**: 학습 특징은 Gazebo에서 관측한
   약 954.93 RPM을 사용했지만 benchmark는 기본값 25 Hz를 사용했다. 실제
   benchmark 관측을 사용하도록 구현을 수정했으며 최종 재측정은 `results/pdm.json`이다.
6. **world 파일 실행 중 표준 라이브러리 이름 충돌**: camera CONFIG 연결 후
   기존 `python3 /app/simulator/world.py` 실행은 `/app/simulator`를 import 경로
   앞에 놓아 이 폴더의 `signal.py`가 Python 표준 `signal`을 가렸다. 시작 스크립트를
   `python3 -m simulator.world /tmp/factory.world`로 고쳤다. 테스트는 실제 subprocess에서
   동일 module invocation을 실행해 정상 종료와 생성된 SDF XML을 확인했다.

## Grad-CAM 계층의 실제 비교

검토 시점 `data/training/vision/weights/best.pt`의 동일한 forward/backward에서
두 계층을 동시에 측정했다. 네 샘플 모두 독립 test 폴더의 첫 이미지이다.
최종 모델의 mAP 선택이나 성능 기준 판정에 이 검토 샘플을 사용하지 않았다.

| test 샘플 | target score | raw `model.9.cv2.conv` 양수 CAM 셀 | post-BN/SiLU `model.9.cv2` 양수 CAM 셀 |
|---|---:|---:|---:|
| 정상 | 0.000800 | 64 / 64 | 56 / 64 |
| 스크래치 | 0.959795 | 0 / 64 | 35 / 64 |
| 찍힘 | 0.978556 | 60 / 64 | 59 / 64 |
| 이물질 | 0.840964 | 35 / 64 | 30 / 64 |

스크래치 raw Conv2d gradient 절댓값 합은 0.263120이었다. 그러나 가중 activation의
최댓값도 -0.000398503이라 ReLU 후 값이 모두 0이었다. 같은 모델의 Conv block
출력에서는 gradient 합 0.437609, 가중 activation 최대 0.000159253이었고 실제
공간적 양수 근거가 존재했다. 이에 최종 구현은 **SPPF 마지막 backbone Conv
block 출력(BN/SiLU 이후)**을 사용하며, 계층 이름과 nonzero/gradient 메타데이터를
정직하게 기록한다. 최종 checkpoint의 생성 이미지는 `results/vision.json`과 함께
평가한다.

수정된 실제 `GradCAM.render()`도 현재 interim checkpoint 하나를 다시 로드해
정상·3종 결함 모두 실행했다. 네 종류 모두 `informative=true`, nonzero gradient와
양수 CAM이 나왔다. 학습 중 best.pt가 갱신되어 위의 초기 비교와 target score가
달라졌으며 각 시점의 수치는 qa.json에 구분했다. 검토자는 interim checkpoint를
따로 보관하지 않았으므로 제출용 재현 결과는 최종 모델 해시와 함께 확인해야 한다.

정상 이미지 target은 별도의 '정상 클래스'가 아니라 가장 큰 불량 score이다.
정상 score가 매우 작아도 정규화된 색은 진하게 보일 수 있으므로 색의 강도만으로
불량 확신도를 비교하면 안 된다. zero CAM은 새로운 색으로 근거처럼 만들지 않고
메타데이터에 비정보적 결과임을 남긴다.

## 데이터 분할과 라벨 확인

센서 특징은 7,000×9이며 모든 값이 finite였다. `provenance.jsonl`의 level/split
순서가 NPZ의 level/split과 정확히 일치했다. 정상/고장 수는 train 3,500/1,400,
val 750/300, test 750/300이다. 정상 전용 AE 학습과 validation threshold 선택,
독립 test 측정을 소스에서도 확인했다.

영상 6,000장의 모든 SHA-256을 실제 JPEG 바이트에서 다시 계산했다. 이미지 경로,
해시, scene ID가 모두 중복 없이 6,000개였다. 정상은 train/val/test 2,100/450/450,
3종 결함 각각은 700/150/150이었다. test 카메라 범위는 train보다 넓고 별도 seed와
장면으로 생성했다. 이미지 메시지의 ground-truth 클래스는 runtime detector 입력
판정에 사용하지 않는다.

정상·스크래치·찍힘·이물질의 실제 Gazebo test 이미지 4장에 라벨 좌표를 표시해
독립적으로 시각 확인했다. 정상 라벨은 비어 있고 나머지 박스는 실제 보이는 결함을
둘러쌌다. 3D primitive 크기·위치와 projection 코드를 비교한 결과도 일치했다.
이것은 4장의 수동 확인이며 전체 6,000장의 수동 정답 검사라는 뜻은 아니다.

## 최종 모델의 독립 확인

최종 `vision.pt`의 SHA-256은
`de2b27a91410cd0ee12167a43b9651a9ef06198e6c86d80fdd2e9d84dffcde57`이다.
변환된 ONNX의 SHA-256은
`e8bc6f517174105955e7b095059e77be7ff1ff222631a5b19d8ca3a03a56d665`이다.

최종 보고서의 원시 지연시간 배열을 독립적으로 다시 계산했다. 두 엔진 모두
동일한 confidence 0.25·NMS IoU 0.45를 사용했다.

| 항목 | 실제 최종 결과 | 조건 |
|---|---:|---|
| 독립 test YOLO mAP50 | 0.969052 | ≥0.80 통과 |
| 가장 낮은 클래스 AP50(찍힘) | 0.945825 | 클래스별 결과 확인 |
| ONNX 100장 평균 | 15.091832 ms / 66.261009 FPS | ≥20 FPS 통과 |
| PyTorch 100장 평균 | 39.299604 ms / 25.445549 FPS | ≥20 FPS 통과 |
| 관리도 / AE test F1 | 0.995025 / 0.988468 | 둘 다 ≥0.80 통과 |
| PdM 5회 평균 | 0.941083 ms | ≤100 ms 통과 |

원시 배열의 길이와 평균은 보고서의 100장/5회·평균·FPS와 일치했다.
PdM은 관측 회전 주파수 15.915494309 Hz를 실제 FFT 특징 추출에 사용했다.
Ubuntu 22.04.5, Python 3.10.12, PyTorch 2.5.1+cpu, OpenCV 4.10.0,
ONNX Runtime 1.20.1의 CPU 환경도 최종 하드웨어 보고서에서 확인했다.
위 mAP/F1은 주 구현의 독립 test 평가이며, 이 검토에서는 test를 활용한
재학습·모델 선택·threshold 조정을 하지 않았다.

동일한 seed 42로 정한 test 이미지 100장에서 **실제 최종 ONNX와 PyTorch의
NMS 후 검출 결과를 다시 비교**했다. 51개 검출의 클래스·수량이 일치했고,
49장은 두 엔진 모두 검출이 없었다. 수량·클래스·수치 허용범위 불일치는 0건이다.
일치 박스의 최소 IoU는 0.9999971914, 최대 좌표 차이는 0.0000419617 pixel,
최대 confidence 차이는 0.00000077486이었다. 이 비교는 ground-truth 라벨을
읽지 않으며, 판정 결과의 동등성을 확인하는 추가 검사이다. 새 정확도나
실행 중인 전체 시스템의 FPS를 측정하는 실험은 아니다.

재현용 스크립트는 `scripts/verify_vision_parity.py`이며, 데이터·PT·ONNX가
준비된 후 아래와 같이 실행한다. 원본·변환 모델·DB·MQTT 상태를 변경하지 않는다.
확인에 쓴 100개 파일 이름은 `qa.json`에 포함했다.

```bash
docker compose run --rm --no-deps vision python3 scripts/verify_vision_parity.py
```

최종 Grad-CAM 네 종류도 `vision.json`에서 실제 nonzero gradient와 positive
activation을 확인했다. 하지만 8×8 backbone map은 정밀한 결함 분할 결과가
아니다. 제품 모서리나 배경에 강한 반응이 보일 수 있으며, nonzero라는 사실은
정확한 결함 위치나 올바른 인과적 판단을 보장하지 않는다. 배경의 shortcut
활용 가능성은 실제 생성 이미지와 함께 해석해야 한다.

추가 소스 검토에서 발견한 카메라 설정 불일치는 SDF의 FOV·영상 크기를 CONFIG에
연결해 해소했다. 재현 스크립트도 데이터 생성 전에 실행 중인 simulator/business
서비스를 멈춰 동일 ROS2 topic에 중복 Gazebo가 발행하지 않도록 바뀌었다.

## 최종 검증과 이 보고서의 한계

오프라인 회귀는 실제 관절 정지·MQTT 전달·DB JPEG BYTEA·실시간 품질 영상·브라우저
동작을 대신하지 않는다. 해당 항목은 통합 실측과 DB 조회가 필요하고, 새 클론에서
Compose 한 명령 실행 검증도 별도로 수행한다. 위 최종 성능은 학습 종료 후
최종 이미지에서 측정한 값이며 JPEG decoding·DB·Grad-CAM 비용은 포함하지 않는다.
Validation mAP를 제출용 test mAP로 쓰지 않는다.

실제 시뮬레이터에서 생성했더라도 결함 형태와 센서 고장식은 단순하다. 서로 다른
해시와 시나리오는 exact-image 누수를 방지하며 실제 공장 일반화나 인과관계를
입증하지 않는다. Pearson/Spearman·시차 해석에도 이 한계를 함께 적용한다.

## 통합 후 추가 회귀

실제 PostgreSQL JSON export에서 일부 UTC 시각의 소수초 `.000`이 생략되어
일반적인 첫 문자열 format 추론이 실패했다. `format='ISO8601'`로 명시하고
`tests/test_iso_timestamps.py`에 소수초 유무와 Z/+00:00 혼합 사례를 추가했다.
주 에이전트가 최종 전체 28개를 격리 실행하여 failures=0,
errors=0, skipped=0을 확인했다. 원본은 `results/tests.xml`이다.
이 최종 실행은 위의 독립 전담 27개 검증에 새로운 실제 실패 회귀를 포함한다.
[pandas 2.2.3 ISO8601 파싱](https://pandas.pydata.org/pandas-docs/version/2.2/reference/api/pandas.to_datetime.html)을 사용했다.
