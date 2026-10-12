# Smart Factory AI 독립 검증

검증일: 2026-10-13(한국 시각). 검토한 구현 커밋: `d200c86db86165e15b5daff4be2e4e6da0a71db6`.

현재 검토한 코드와 격리 실행에서는 팀 역할 항목을 제외한 필수 기능과 세 선택 과제의 구현을 막는 결함을 발견하지 않았다. 새 커밋을 빈 데이터·새 DB 환경에서 한 명령으로 기동했고 실제 모델 출력까지 확인했다. **최종 제출용 공개 저장소에 이 커밋이 게시됐는지는 이번 검증에서 확인하지 않았으므로, 공개 제출까지 전부 완료했다는 판정은 아직 하지 않는다.**

검증자는 주 구현자의 성공 문구를 그대로 채택하지 않았다. 원 미션, 코드·설정·SQL·문서, 원본 joint/seed 기록, 체크포인트, 실제 PostgreSQL, 새 clone 실행을 대조했다. production 코드와 기존 시뮬레이터 제어는 수정하지 않았고 자체 검증 코드와 결과만 이 폴더에 작성했다.

## 직접 재검산한 결과

| 검증 | 독립 결과 | 근거 |
|---|---|---|
| RUL 원본 재생 | 11,199창 전체의 joint·seed로 파형 및 9특징 재생. 최대 절대 오차 `2.220446049250313e-15` | `independent-data-review.py`, `independent-data-review.json` |
| RUL 누출·라벨 | 개체 분리 56/12/12, 교차 개체 누출 0. 남은 시간 라벨, 순차 age/새 joint 시각, UTC, feature SHA, 물리 정지 끝점 일치 | 같은 파일 |
| RUL 네트워크 실행 | 프로젝트 모델 코드를 import하지 않고 LSTM을 별도로 작성. test 12개 개체/1,361개 시퀀스의 예측 차이 0.0초. MAE 3.6478126263초 | `independent-model-review.py`, `independent-model-review.json` |
| PdM 필수 정확도 | 독립 MLP·혼동행렬 계산: 관리도 TP300/FP3/FN0/TN747, F1 0.9950248756. AE TP300/FP7/FN0/TN743, F1 0.9884678748 | `independent-required-model-review.py`, `.json` |
| RUL 수신 간격 | 같은 연속 추세를 1초·2초·불규칙 간격으로 보내 모두 74.67초 예측. 실제 시간 slope .002 일치. 긴 공백 후 초기화 | `independent-runtime-review.py`, `.json` |
| PSI 공식·꼬리·평가주기 | 실제 고정 reference의 4특징에 별도 histogram/자연로그 공식을 적용. PSI 4.7064095036, 구현과 차이 0.0. 128창 이전 미평가 확인 | 같은 파일 |
| 실제 DB | 읽기 전용 트랜잭션. 연속 fault_level DOUBLE PRECISION, JSON/컬럼 불일치 0, prognostics→health 누락/시각 불일치 0. wear RUL 추정 109행, 실제 threshold를 넘은 evaluated 이벤트와 연결된 drift 알람 1행 | `independent-db-review.py`, `.json` |
| 함수·클래스·이력 | 현재 제출 작업 브랜치 AST 정의 368개, 누락 docstring 0, 현재 브랜치 Conventional Commit 위반 0 | `independent-code-quality.json` |
| CPU Vision 비교 | 같은 frozen test 100장, warmup 10장, 실제 측정 PyTorch 4스레드. ONNX 22.6656 FPS / 44.1197ms, PyTorch 5.1335 FPS / 194.7986ms | `independent-vision-speed.py`, `.json` |

Vision 속도는 두 Gazebo 실행과 다른 검증 작업이 겹친 부하 조건에서 측정했다. ONNX는 CPU 20 FPS 요구를 통과했다. PyTorch가 20 FPS를 넘었다고 주장하지 않는다. 기존 문서의 25.45 FPS와 같은 부하 조건의 재현으로 해석하지 않는다. 두 엔진 모두 전처리·추론·NMS를 포함했고 JPEG 디코딩, 파일 IO, CAM, 네트워크·DB 저장은 제외했다. 개별 모델 throughput과 전체 생산라인 처리율을 구분해야 한다.

기존 Vision mAP50 0.969052 및 PdM 전체 추론시간·Grad-CAM·전후 인터락 결과는 주 구현자의 기존 원본 평가와 이번 추가 실행 기록, 해당 코드·모델 해시를 검토했다. 이 검증자가 별도로 900장 mAP 전체평가나 실제 제어 명령을 다시 실행한 결과로 표현하지 않는다.

## 새 clone의 한 명령 실행

Windows 작업 저장소의 위 커밋을 `/home/gkwlgns02/smart-factory-independent-clean-20261013`에 새로 clone했다. 기동 전에 Git 작업 디렉터리가 깨끗하고 `data/`와 `artifacts/vision.onnx`가 없으며, 세 체크포인트와 PSI reference가 Git 추적 파일임을 확인했다.

새 project `smart-factory-independent-clean-20261013`, 포트 8085에서 `docker compose up -d --build` 한 번으로 기동했다. MQTT/DB를 포함한 9서비스가 실행됐고, Vision ONNX 및 export sidecar가 자동 생성됐다. 읽기 전용 HTTP 관측에서 센서 27개·검사 17개·불량 2개 저장, 9특징·FFT·HI·검사 이력을 확인했다. RUL은 24관측의 충분한 시간 이력 후 `outside_wear_scenario`, PSI는 `warming_up` 24관측이었다. 정상 정적 신호에 유한한 수명을 만들어 표시하지 않았다.

새 clone을 별도 네트워크 차단·소스 읽기 전용 컨테이너에서 검사한 결과 **149 passed, 2 skipped, 0 failed**였다. 두 skip은 clone에 학습 원본을 포함하지 않아 생긴 센서/영상 원본 의존 검사다. 주 구현자의 원본 데이터 연결 최종 검사는 **151 passed, 0 skipped**로 기록돼 있다.

검증 후 자체 project만 `docker compose down`했고 새 DB/MQTT 두 볼륨은 `docker volume inspect`로 보존 확인했다. 기존 8084 project와 원본 v1/r3 데이터는 제어·삭제하지 않았다.

근거: `independent-clean-deploy.sh`, `independent-clean-deploy.log`, `independent-clean-source.txt`, `independent-clean-tracked-artifacts.txt`, `independent-clean-startup.json`, `independent-clean-tests.xml`, `independent-clean-services-final.jsonl`, `independent-clean-artifact-hashes.txt`, `independent-clean-cleanup.log`, `independent-clean-volumes-preserved.json`.

## 발견 후 보완을 확인한 사항

1. RUL이 가변 간격을 고정 1초 입력으로 오해하던 문제: 과거 관측을 실제 시각의 1초 격자로 재표본화하고 초당 slope를 계산하도록 수정됐고 독립 간격 테스트를 통과했다.
2. RUL checkpoint가 Git에서 누락돼 새 clone의 prognostics가 무한 대기하던 문제: `rul.pt`·reference·model card가 추적되며 새 clone에서 실제 준비 완료했다. 추가 실험 재현 스크립트도 제공한다.
3. RUL/PSI 미구현으로 남아 있던 문서: 현재 구현·데이터·시간 단위·재현 절차로 갱신됐다.
4. 연속 wear의 DB 정수 반올림: 새 스키마 및 기존 볼륨의 additive 변환이 DOUBLE PRECISION이며 실제 JSON/컬럼 불일치 0으로 확인했다. wear age/level은 capture 후 다시 계산한다.
5. 원인 검증 실패가 exit 0으로 끝나던 문제: 실패 시 예외로 종료하며 행수·중복 arm 검사도 추가됐다.
6. 원인 보고서의 존재하지 않는 소스 해시 주장: 실제 보존한 데이터·영상·모델 해시로 문구를 수정했다.

## 해석과 최종 제출의 경계

RUL의 개체는 한 Gazebo 모터에서 다른 seed·wear·RPM·noise 시나리오로 만든 가상 궤적이다. 파손 베어링의 실제 물리 수명 검증이 아니다. 실제 온라인 추가 기록은 91개 RUL 추정, MAE 4.4543초/RMSE 6.2430초 및 PSI 알람의 DB→HTTP 노출을 보여준다. 이 온라인 한 궤적은 독립 12개 개체 시험을 대체하지 않는다.

60쌍의 Fault_Level 0/5 개입과 paired bootstrap은 시뮬레이터에 선언한 구조적 원인의 결과를 확인한다. Fault_Level이 진동과 결함 확률을 직접 조절하는 구현이므로 실제 공장의 원인을 발견했다고 해석할 수 없다. PSI도 고장 확률이나 실제 공장의 보편 임계값이 아니다.

기존/후보 강건성 추가 평가의 WARN, 실제 공장·저대비 결함 UNVALIDATED 기록은 그대로 유효하다. 필수 합성 벤치마크 통과를 모든 운전 조건에서 오류가 없다는 뜻으로 확대하지 않는다.

마지막으로, Conventional Commits 검사는 **현재 제출 작업 브랜치의 이력**을 대상으로 했다. 기존 공개 default branch가 자동으로 교정됐다고 간주하지 않았다. 최종 요구사항 완료 보고에는 새 구현 커밋의 공개 게시와 실제 제출 브랜치의 이력 확인을 별도로 포함해야 한다.
