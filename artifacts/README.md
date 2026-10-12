# 학습 모델

`pdm.pt`: 정상 train 3,500창으로 학습한 AE, 정상 특징 통계와 validation 교정.
`vision.pt`: Gazebo 영상 train 4,200장으로 파인튜닝한 YOLOv8n validation-best checkpoint.
`vision.onnx`: 첫 실행에 vision.pt에서 CPU export하는 ONNX FP32 그래프(미포함).
`rul.pt`: 가상 wear80개체 중train56개체로 학습하고 validation12개체로 선택한 LSTM.
`drift-reference.json`: v1 정상 train3,500창으로만 맞춘 고정 PSI histogram.

모델 SHA-256, 파일 크기, 학습 데이터·예산과 하드웨어는 `model-card.json`에 있다.
원본 checkpoint는 synthetic 실험 범위의 성능을 보이며 실제 공장 일반화 검증은 없다.
추가 모델의 시간 단위·데이터 해시·입력 제한은 `prognostics-model-card.json`과 `docs/prognostics.md`에 있다.
