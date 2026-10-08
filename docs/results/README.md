# 실측 결과 원본

| 파일 | 생성 단계 |
|---|---|
| dataset.json | Gazebo 영상·센서 데이터 생성 완료 |
| pdm.json | 정상 AE 학습·독립 test 평가·5회 CPU benchmark |
| vision.json | YOLO 파인튜닝·독립 test 평가·100장 CPU benchmark |
| sample_*.jpg / gradcam_*.jpg | 실제 test 영상·backbone 역전파 시각화 |
| integration.json | 실제 localhost API·MQTT·Gazebo 롤러 인터락 검증 |

해당 파일이 없으면 그 실험은 완료되지 않은 상태다. 과제의 예시 출력값을
실험 결과로 복사하지 않는다. 목표와 실제 통과 여부를 구분한다.
