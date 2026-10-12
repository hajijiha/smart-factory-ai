# 검증 보고서

검증 범위는 회귀 테스트, 데이터 무결성, 모델 평가·변환, DB 저장과 물리 인터락이다.
측정 원본은 `results/qa.json`, JUnit 결과는 `results/tests.xml`에 있다.

이 문서는 기존 v1 모델·데이터의 측정 기록이다. v2 데이터 생성과 편향 검증 추가 후
전체 회귀 검사는 81개가 통과했으며, 결과는 `results/bias-v2/tests.xml`에 있다.
새 검사·파일럿 실험의 범위와 한계는 [합성 데이터 편향 검증](bias-validation.md)에 정리했다.

## 회귀 테스트

2026-10-08 CPU 런타임에서 **28 passed / 0 failed / 0 skipped**를 기록했다.
최종 실행 시간은 5.105초다. 테스트는 네트워크가 없는 컨테이너에서 소스를
읽기 전용으로 마운트해 실행했으며, 실행 중인 MQTT·DB 상태를 변경하지 않았다.

```bash
docker compose build
docker run --rm --network none --cpus=2 \
  -v "$PWD:/app:ro" -w /app \
  smart-factory-runtime:local \
  python3 -m pytest tests -q -p no:cacheprovider
```

| 영역 | 검사 |
|---|---|
| FFT | 사인파 RMS·주파수·진폭, 베어링 주파수, Nyquist |
| 진단 | Score·HI 범위와 정상/위험 상태 |
| 인터락 | 위험 반복 메시지, 정지 래치, 수동 재시작 |
| 재시작 조건 | HI와 라인 관측의 0~5초 freshness, 레벨 0, HI≥80 |
| 시각 처리 | 미래·오래된·잘못된·시간대 없는 시각 거절, ISO8601 소수초 유무 |
| 상관 | 미검사 구간을 유지한 5초 시차 |
| Vision | 최초 ONNX export, 전처리, 좌표 복원, 클래스별 NMS |
| 데이터 | JPEG 해시·경로·scene 중복, 센서 라벨·분할·출처 정합 |
| Gazebo | YAML FOV·영상 크기의 SDF 반영, module 명령 실행 |

관련 검사는 `tests/test_review_*.py`, `test_interlock.py`, `test_iso_timestamps.py`에 있다.
생성 데이터가 없는 clone에서는 데이터 의존 검사 2개가 skip된다.
Matplotlib/Pyparsing 의존성의 deprecated API 경고 14건이 발생했다.

## 수정한 결함과 재발 방지

| 결함 | 원인 | 수정·검증 |
|---|---|---|
| 최초 ONNX export 실패 | `vision.service`의 CONFIG import 누락 | import 추가, PT만 존재하는 최초 실행 회귀 |
| 미래·오래된 관측으로 재시작 | HI의 음수 age와 라인 시각 미검사 | HI·라인 모두 `0≤age≤5`, timezone 검사 |
| 스크래치 CAM 전체 0 | raw Conv 출력의 가중 activation이 음수 | 최종 Conv block의 BN/SiLU 이후 출력 사용 |
| 정지 명령 손실 | ROS 콜백의 단일 슬롯 덮어쓰기 | C++ FIFO, scene에 현재 running 상태 전달 |
| PdM benchmark 회전수 불일치 | 관측 RPM 대신 기본 25 Hz 사용 | 관측 15.915494 Hz로 FFT 포함 재측정 |
| world 생성의 import 충돌 | 경로 실행 시 로컬 signal.py가 표준 signal을 가림 | `python3 -m simulator.world`, subprocess 회귀 |
| ISO 시각 파싱 실패 | PostgreSQL JSON의 선택적 `.000` 소수초 | `format='ISO8601'`, 혼합 Z/+00:00 회귀 |

ISO 처리 API: [pandas 2.2.3](https://pandas.pydata.org/pandas-docs/version/2.2/reference/api/pandas.to_datetime.html).

## 데이터 무결성

센서 특징은 7,000×9이며 모든 값이 finite다. `provenance.jsonl`과 NPZ의
level·split 순서가 일치한다. 정상/고장은 train 3,500/1,400, validation 750/300,
test 750/300이다. AE는 정상 train만 학습하며 threshold는 validation에서 선택한다.

영상 6,000장의 JPEG 바이트로 SHA-256을 재계산했다. 경로·해시·scene ID에 중복이 없다.
정상 train/validation/test는 2,100/450/450, 각 결함은 700/150/150이다.
별도 seed와 카메라·조명 조건으로 test 장면을 생성했다.
정상·결함별 표본 4장의 박스를 수동 확인했으며, 전체 영상의 수동 정답 검사는 아니다.
ground-truth 클래스와 fault_level은 운영 detector의 예측 입력에 사용하지 않는다.

## 모델 평가와 변환

최종 `vision.pt` SHA-256:
`de2b27a91410cd0ee12167a43b9651a9ef06198e6c86d80fdd2e9d84dffcde57`

평가에 사용한 ONNX SHA-256:
`e8bc6f517174105955e7b095059e77be7ff1ff222631a5b19d8ca3a03a56d665`

| 항목 | 결과 | 조건 |
|---|---:|---|
| YOLO test mAP50 | 0.969052 | test 900장 |
| 가장 낮은 클래스 AP50(찍힘) | 0.945825 | 클래스별 평가 |
| ONNX | 15.091832 ms / 66.261009 FPS | 전처리·NMS 포함 100장 |
| PyTorch | 39.299604 ms / 25.445549 FPS | 동일한 100장 |
| 관리도 / AE F1 | 0.995025 / 0.988468 | test 1,050창 |
| PdM 평균 | 0.941083 ms | FFT·특징·두 모델·HI, 5회 |

원시 지연 배열의 평균과 FPS를 다시 계산해 보고서 수치와 대조했다.
환경은 Ubuntu 22.04.5, Python 3.10.12, PyTorch 2.5.1+cpu, OpenCV 4.10.0,
ONNX Runtime 1.20.1이다. detector FPS에는 JPEG 디코딩·DB·네트워크·CAM을 포함하지 않는다.

seed 42의 동일 test 이미지 100장을 confidence 0.25 / NMS IoU 0.45로 비교했다.
검출 51개의 클래스·수량이 일치했고, 49장은 두 엔진 모두 검출이 없었다.
허용범위 밖 불일치는 0건이다. 최소 박스 IoU는 0.9999971914,
최대 좌표 차이는 0.0000419617 px, 최대 confidence 차이는 0.00000077486이다.
이 비교는 라벨을 사용하지 않는 엔진 동등성 검사다.

```bash
docker compose run --rm --no-deps vision python3 scripts/verify_vision_parity.py
```

## Grad-CAM 계층 비교

학습 중 체크포인트의 동일한 forward/backward에서 raw Conv와 Conv block 출력을 비교했다.
비교 표본은 각 test 클래스의 첫 이미지이며 checkpoint 선택에 사용하지 않았다.

| 표본 | target score | raw model.9.cv2.conv 양수 셀 | post-BN/SiLU model.9.cv2 양수 셀 |
|---|---:|---:|---:|
| 정상 | 0.000800 | 64/64 | 56/64 |
| 스크래치 | 0.959795 | 0/64 | 35/64 |
| 찍힘 | 0.978556 | 60/64 | 59/64 |
| 이물질 | 0.840964 | 35/64 | 30/64 |

스크래치 raw Conv의 gradient 합은 0.263120이지만 가중 activation의 최댓값이
-0.000398503이라 ReLU 후 0이었다. Conv block 출력에서는 gradient 합 0.437609,
activation 최대 0.000159253을 기록했다. 최종 구현은 `model.9.cv2`의 BN/SiLU 이후 출력을 사용한다.
이 중간 checkpoint는 별도 배포하지 않는다. 최종 모델의 CAM 수치와 영상은
`results/vision.json`과 [Vision 문서](vision.md)에 있다.

최종 정상·3종 결함 표본은 모두 nonzero gradient와 양수 activation을 보였다.
정상 영상의 target은 가장 큰 결함 score이며 정상 클래스 확률이 아니다.
이미지마다 정규화한 색의 강도를 확신도처럼 비교할 수 없다.
8×8 map과 제품 경계·배경의 반응은 정밀한 분할이나 인과적 설명을 보장하지 않는다.

## 통합·저장·최초 기동

| 검증 | 결과 원본 |
|---|---|
| 정상 이력 증가, 위험 정지, unsafe reset 거절, 정상 회복·수동 재시작 | `results/integration.json` |
| 센서 hypertable, JPEG BYTEA·디스크 일치, 동일 event의 HI·CAM | `results/storage.json` |
| 반복 운전의 Pearson·Spearman·0~30초 시차 | `results/correlation.json` |
| 공개 clone의 단일 Compose 기동 | `results/deployment.json` |

구현 커밋 `0c949db3a40e1c0e78213a2daa3b44c63fe5ce5e`를 새 폴더에 clone하고
별도 네트워크·MQTT·DB 볼륨과 포트 8081로 실행했다.

```bash
DASHBOARD_PORT=8081 docker compose -p smart-factory-clean-check up -d --build
```

데이터·ONNX가 없는 상태에서 자동 export, 8개 서비스, 최근 센서·라인 상태,
검사 5건·센서 12건과 JPEG 응답을 확인했다. Docker 의존성 캐시를 재사용한 조건에서
첫 검사 결과까지 74.408초였다. 최초 네트워크 다운로드 시간은 이 측정에 포함되지 않는다.

## 해석 범위

단순한 결함 형상과 합성 센서 고장식에 대한 검증이다. 데이터 중복이 없더라도
실제 공장 일반화와 인과관계가 입증되는 것은 아니다.
상관·시차는 공통 고장 레벨, 집계 구간과 유한 표본의 영향을 받는다.
검출 성능, 설명 영상과 물리 인터락은 각각의 검증 결과로 평가한다.
