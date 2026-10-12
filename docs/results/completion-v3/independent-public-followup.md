# 공개 제출 후속 독립 확인

2026-10-13 한국 시각 기준, [공개 저장소](https://github.com/hajijiha/smart-factory-ai)의 제출 상태를 추가 확인했다. 앞선 `independent-review.md`는 당시의 외부 게시 미확인 상태를 기록한 원본으로 보존했다.

GitHub connector metadata 및 인증 헤더 없이 조회한 공개 REST API에서 `private=false`, `visibility=public`, `default_branch=main`을 확인했다. `git ls-remote --symref`와 실제 fetch 결과에서도 기본 HEAD는 main이며, **main과 codex/complete-requirements는 모두 `56642e6a56fbecdb56e3e09b55b94d498ac9d4ab`**였다.

`archive/pre-completion-20261013`은 이전 공개 커밋 `1b4dfba66ee5ffaefb112ea4ab9eec80cf920339`를 그대로 가리킨다. 과거의 형식 위반 3개 커밋은 보존 브랜치에 남아 있고, **제출 main의 전체 16개 커밋에는 Conventional Commits 위반이 없다.** 보존된 역사와 제출 이력을 구분한 판정이다.

새 clone에서 실제 실행 검증한 `d200c86`과 게시된 `56642e6`을 Git tree로 대조했다. `factory/`, `simulator/`, `compose.yaml`, `config.yaml`, `docker/`, `requirements.txt`, `artifacts/`, `.env.example`의 변경은 **0개**다. PdM·Vision·RUL 체크포인트, PSI reference, prognostics model card의 Git blob도 각각 동일하다. 후속 변경은 문서·증거, 그림 생성 스크립트, 품질 검사의 docs 포함, 재현 스크립트의 검증 명령을 컨테이너에서 실행하도록 한 변경이다. 기존 clean startup·모델 검증 결과를 게시된 서비스 코드에도 적용할 수 있다.

이 후속 확인은 **56642e6까지 실제 게시된 상태**를 대상으로 한다. 이후 증거만 추가한 커밋이 생기면 새 원격 tip과 해당 커밋의 Conventional 형식 및 변경 경로를 확인하면 된다. 실행 소스·환경·모델이 동일한 증거 추가만으로 기존 기능 회귀 검사를 반복할 필요는 없지만, 아직 확인하지 않은 미래 SHA를 이번 보고서에서 검증 완료로 주장하지 않는다.

결론: 앞서 남았던 **공개 게시와 제출 main 이력 확인도 통과**했다. 실제 공장 일반화, 강건성 WARN 및 합성 RUL/원인 실험의 범위 제한은 앞선 독립 보고서에 기록한 그대로다.

전체 근거는 `public-submission.json`, `public-repository-snapshot.json`, `public-anonymous-metadata.json`, `independent-public-submission.py`에 보존했다.
