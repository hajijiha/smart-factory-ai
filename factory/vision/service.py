"""MQTT image inspection worker; defect Grad-CAM runs outside the detector path."""
import base64
import concurrent.futures
import logging
import queue
import time
import cv2
import numpy as np
from ultralytics import YOLO
from factory.common import ARTIFACTS, DATA, connect, publish
from factory.vision.inference import VisionDetector
from factory.vision.gradcam import GradCAM


def main():
    """Inspect simulator image messages, save evidence and publish quality events."""
    while not (ARTIFACTS/'vision.pt').exists():
        logging.info('Waiting for trained vision artifacts')
        time.sleep(5)
    if not (ARTIFACTS/'vision.onnx').exists():
        YOLO(str(ARTIFACTS/'vision.pt')).export(format='onnx',imgsz=CONFIG['vision']['image_size'],opset=17,simplify=False,device='cpu')
    detector = VisionDetector(ARTIFACTS/'vision.onnx')
    cam = GradCAM(ARTIFACTS/'vision.pt')
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    events = queue.Queue(maxsize=128)
    def handler(topic,message):
        """Hand off inference to the worker thread."""
        events.put_nowait(message)
    client = connect('vision',['factory/image'],handler)
    image_dir = DATA/'inspections'
    image_dir.mkdir(parents=True,exist_ok=True)
    def heatmap(image,result):
        """Compute one actual backbone Grad-CAM and publish its artifact update."""
        try:
            class_id = result['detections'][0]['class_id']
            overlay,metadata = cam.render(image,class_id)
            path = image_dir/f'{result["event_id"]}_gradcam.jpg'
            cv2.imwrite(str(path),overlay)
            publish(client,'factory/gradcam',{'event_id':result['event_id'],'timestamp':result['timestamp'],
                    'gradcam_path':str(path.relative_to(DATA)),'metadata':metadata})
        except Exception:
            logging.exception('Grad-CAM failed for %s',result['event_id'])
    while True:
        message = events.get()
        image = cv2.imdecode(np.frombuffer(base64.b64decode(message['image_base64']),dtype=np.uint8),cv2.IMREAD_COLOR)
        start = time.perf_counter()
        detections = detector.infer(image)
        latency = (time.perf_counter()-start)*1000
        path = image_dir/f'{message["event_id"]}.jpg'
        cv2.imwrite(str(path),image)
        result = {'event_id':message['event_id'],'timestamp':message['timestamp'],'image_path':str(path.relative_to(DATA)),
                  'detections':detections,'defective':bool(detections),'inference_ms':latency}
        publish(client,'factory/quality',result)
        if detections:
            executor.submit(heatmap,image.copy(),result.copy())
        logging.info('Vision detections=%d CPU %.2fms',len(detections),latency)


if __name__ == '__main__':
    main()
