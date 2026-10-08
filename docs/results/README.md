# 실제 평가 원본

- dataset.json / dataset-audit.json: 생성 완료 수량과 전체 데이터 무결성.
- pdm.json / hardware.json: 독립 F1, 실제 RPM FFT 포함 5회 지연과 CPU 환경.
- vision.json / training.json / vision-training.csv: 독립 test mAP, 100장 지연,
  학습 예산과 validation 선택. validation mAP를 test 결과로 사용하지 않았다.
- qa.json / tests.xml: 독립 검증과 통합 후 전체 28개 회귀 원본.
- integration.json / storage.json: 실제 물리 정지·복구와 이미지 BYTEA/event HI.
- correlation-observations.json / correlation.json: 실제 관측, 집계, 0–30초 시차.
- sample/gradcam 이미지, 과학 그래프와 dashboard 스크린샷: 실제 생성·실행 증거.
- deployment.json: 공개 구현 커밋의 별도 clone 최초 기동 통과; 캐시 사용 조건 포함.

목표 수치·참고 예시를 실측값으로 대체하지 않았다. 런타임 건수와 대시보드
상관은 시간이 흐르며 변하고, 문서 실험은 저장된 고정 관측 구간을 사용한다.
