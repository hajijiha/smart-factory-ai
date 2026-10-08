# 모델 산출물

`pdm.pt`: 정상 창으로 학습한 AE 가중치, 정상 특징 통계, 검증 임계값·HI 교정.
`vision.pt`: Gazebo 영상으로 파인튜닝한 YOLOv8n best validation checkpoint.
`vision.onnx`: 실행 시 vision.pt에서 생성하는 ONNX FP32 그래프.

학습이 완료되기 전에는 checkpoint를 임의 값으로 만들지 않는다.
데이터 생성·학습·평가 명령은 루트 README와 scripts/reproduce.sh에 있다.
최종 가중치의 SHA256 및 학습 조건은 실제 생성 후 모델 카드에 기록한다.
