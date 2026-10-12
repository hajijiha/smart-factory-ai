# LSTM 수명 예측과 PSI 분포 감시

세 선택 과제인 LSTM RUL, ONNX와 PyTorch 비교, PSI 감시·알람을 포함한다.
ONNX의 같은100장 비교와 전처리·NMS 포함 속도는 [영상 검사 문서](vision.md)에 있다.
추가 서비스는 MQTT `factory/health`를 구독하고 `factory/prognostics`와 `factory/alarm`을 발행한다.
저장·진단·제어와 별도 프로세스로 실행하며 대시보드는 DB의 실제 예측을 표시한다.

## 수명 데이터와 평가

Gazebo의 실제 관절 속도·위상을 취득한 후 명시적인 단조 wear를 가상 진동 센서에 주입한다.
80개 가상 개체를 취득 전에 train56/validation12/test12로 나누었다. 수명은100–179가상 운전초이며
초기 wear·증가 지수·RPM·노이즈를 시드별로 다르게 설정했다. 총11,199창이다.
고장 정의는 주입 wear가9에 도달하는 기능적 실패 대용값이며 끝점마다 실제 롤러 정지를 확인했다.
이는 손상된 베어링의 물리 마모 모델이 아니다. 가상 개체는 하나의 시뮬레이터를 서로 다른 시나리오로 운전한 것이다.

입력은 과거24창의9진동 특징이다. LSTM hidden32와 회귀 head16을 사용한다.
age, failure time, wear level, RUL label, 개체 ID와 split은 입력하지 않는다.
평균·표준편차와 상수 기준선은 train만 사용하고 최소 validation MAE epoch를 선택한다.
선택한 checkpoint를 저장한 뒤 test를 한 번 평가한다.

| 평가 | 결과 |
|---|---:|
| test 개체 / 시퀀스 |12 /1,361 |
| MAE |3.6478가상 운전초 |
| RMSE |5.2460가상 운전초 |
| 예측 편향 |−.8948가상 운전초 |
| train 중앙값 기준선 MAE |31.3688가상 운전초 |
| 선택 epoch / validation MAE |49 /5.3165가상 운전초 |

[수치·각 시험 예측](results/completion-v3/rul.json), [취득 설정·해시](results/completion-v3/rul-dataset.json)를 보존한다.
독립 검증자는11,199창을 joint와 RNG로 재생했고 특징 최대 오차는2.22e−15였다.
프로젝트 모델 코드를 import하지 않고 LSTM을 별도로 작성해 시험 forward를 재계산했으며 저장 예측과 차이0이었다.

온라인은 가변 수신 간격을 UTC 기준1초 격자로 선형 재표본화한다. 사용 시점까지 도착한 과거 관측만 사용한다.
23초 이상의 이력이 필요하고 RMS의 초당 증가 추세가 .00025 이상일 때만 유한한 수명을 표시한다.
정상·회복 상태는 `outside_wear_scenario`, 초기화·공백 상태는 `warming_up`이다.
예측값의 단위는 교정된 합성 wear 시나리오의 가상 운전초이며 실제 설비의 벽시계 수명으로 해석하지 않는다.

실제 연속 열화 실행에서는91건을 예측했고 MAE4.454초/RMSE6.243초였다.
같은 실행에서 PSI4.706 경고를 DB에 저장하고 HTTP로 확인했다.
[온라인 원본](results/completion-v3/optional-runtime.json)에 참값·예측·PSI·알람을 함께 기록한다.
별도 실행의 [위험 정지·복구](results/completion-v3/process-runtime.json)와
[일반 운전 복귀 후 재검증](results/completion-v3/process-runtime-restored.json)도 통과했다.

## 고정 PSI 기준과 알람

원본 v1의 정상 train3,500개로만 RMS·spectral energy·BPFO·BPFI의8개 분위수 bin을 맞춘다.
양끝 bin을 개방해 새로운 극단값도 센다. reference를 live/test 데이터로 갱신하지 않는다.
각 bin의 smoothing은 .5, 공식은 `sum((q-p)*ln(q/p))`이다.
현재128개 창과 기준 분포를 비교하며32개 창마다 평가한다.4특징의 최대 PSI가 .25 이상이면 drift다.
stable→drift 전환에 warning 알람을 한 번 발행하고 stable 회복 후 다시 발행할 수 있다.
중복·역순·stale/future 관측은 제외하고5초 초과 공백은 이력을 초기화한다.

PSI는 고장 확률이나 인과 진단이 아니다. 운전 조건, 센서 변화와 고장이 모두 원인이 될 수 있다.
고정 .25는 이 프로젝트의 감시 기준이며 모든 실제 공정의 보편 임계값으로 주장하지 않는다.

## 재현과 운전

일반 `docker compose up --build`는 공개된 RUL checkpoint와 PSI reference를 사용한다.
원본 센서 데이터가 필요한 재학습은 전체 재현 스크립트 또는 아래 별도 스크립트로 수행한다.

```bash
bash scripts/reproduce.sh
# 기존 v1 sensor/dataset.npz가 있을 때 새 추가 실험만 수행
OPTIONAL_RUN=optional-new-run bash scripts/run_optional_validation.sh
```

새 실험은 별도 data/artifacts/results 경로에 생성하며 기존 출력이 있으면 중단한다.
`PSI_REFERENCE_DATA`로 컨테이너의 정상 학습 dataset 위치를 지정할 수 있다.
공개 기준 모델을 새 실험 모델로 자동 교체하지 않는다.

실제 온라인 연속 열화 확인은120초 수명 프로파일로 수행한다. 명령은 로컬 시뮬레이션의 상태를 바꾼다.

```bash
LIVE_WEAR_SECONDS=120 docker compose up -d --force-recreate simulator prognostics
docker compose run --rm --no-deps pdm python3 scripts/verify_optional_runtime.py --url http://dashboard:8080
docker compose up -d --force-recreate simulator prognostics
docker compose run --rm --no-deps pdm python3 scripts/verify_process.py --url http://dashboard:8080
```

wear 참값은 분석용 metadata로만 전송한다. 위험 인터락은 그대로 작동하며 롤러가 멈춰도 시험대 모터는 진단을 계속한다.
일반 운전으로 복귀한 후 HI가80 이상일 때 안전 재시작한다.
