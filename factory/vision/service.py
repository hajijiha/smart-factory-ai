"""MQTT image inspection worker; defect Grad-CAM runs outside the detector path."""
import base64
import concurrent.futures
import hashlib
import logging
import queue
import re
import threading
import time
import cv2
import numpy as np
import torch
from ultralytics import YOLO
from factory.common import ARTIFACTS, DATA, CONFIG, connect, publish
from factory.vision.inference import VisionDetector
from factory.vision.gradcam import GradCAM
from factory.provenance import (read_training_record, require_runtime_generator,
                                vision_export_current, write_export_record, sha256_file)
from factory.world_state import IDENTITY_FIELDS, CAMERA_MAX_BYTES, vector


def inspection_identity(message):
    """Copy observation provenance without exposing simulator defect ground truth."""
    identity = {key: message[key] for key in IDENTITY_FIELDS if message.get(key) is not None}
    for key in ('pose_alignment', 'camera_receipt_age_seconds_at_capture', 'sensor_sim_time_offset_seconds',
                'capture_timestamp', 'delivery_acknowledged_at', 'delivery_delay_seconds'):
        if message.get(key) is not None:
            identity[key] = message[key]
    identity.setdefault('camera_timestamp', message['timestamp'])
    identity.setdefault('camera_sim_time', message.get('sim_time'))
    identity.setdefault('sensor_event_id', message['event_id'])
    return identity


def evidence_path(directory, event_id, suffix=''):
    """Allow opaque evidence identifiers while preventing path traversal via MQTT."""
    if not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', event_id):
        raise ValueError('Invalid inspection event identifier')
    return directory / f'{event_id}{suffix}.jpg'


def product_detections(detections, roi, image_shape):
    """Keep boxes whose centers lie inside the independently observed plate bounds.

    Bounds are inclusive and measured in image pixels. Invalid/missing target
    bounds reject the inspection instead of converting it into a normal result.
    """
    height, width = image_shape[:2]
    if not vector(roi, 4) or not (0 <= roi[0] < roi[2] <= width and 0 <= roi[1] < roi[3] <= height):
        raise ValueError('Product inspection requires valid observed image bounds')
    selected = []
    for detection in detections:
        box = detection.get('bbox')
        if not vector(box, 4) or not (box[0] <= box[2] and box[1] <= box[3]):
            raise ValueError('Detector returned an invalid bounding box')
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if roi[0] <= x <= roi[2] and roi[1] <= y <= roi[3]:
            selected.append(detection)
    return selected


def main():
    """Inspect simulator image messages, save evidence and publish quality events."""
    torch.set_num_threads(CONFIG['vision']['threads'])
    cv2.setNumThreads(1)
    while not (ARTIFACTS/'vision.pt').exists():
        logging.info('Waiting for trained vision artifacts')
        time.sleep(5)
    require_runtime_generator(read_training_record(ARTIFACTS, 'vision'))
    if not vision_export_current(ARTIFACTS):
        YOLO(str(ARTIFACTS/'vision.pt')).export(format='onnx',imgsz=CONFIG['vision']['image_size'],opset=17,simplify=False,device='cpu')
        write_export_record(ARTIFACTS)
    detector = VisionDetector(ARTIFACTS/'vision.onnx')
    model_version = 'sha256:' + sha256_file(ARTIFACTS/'vision.pt')
    cam = GradCAM(ARTIFACTS/'vision.pt')
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    cam_slots = threading.BoundedSemaphore(2)
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
            path = evidence_path(image_dir, result['event_id'], '_gradcam')
            cv2.imwrite(str(path),overlay)
            publish(client,'factory/gradcam',{'event_id':result['event_id'],'timestamp':result['timestamp'],
                    'gradcam_path':str(path.relative_to(DATA)),'metadata':metadata, **inspection_identity(result)})
        except Exception:
            logging.exception('Grad-CAM failed for %s',result['event_id'])
        finally:
            cam_slots.release()
    while True:
        message = events.get()
        try:
            path = evidence_path(image_dir, message['event_id'])
            encoded = message['image_base64']
            if not isinstance(encoded, str) or len(encoded) > CAMERA_MAX_BYTES * 4 // 3 + 8:
                raise ValueError('Inspection image exceeds maximum size')
            payload = base64.b64decode(encoded, validate=True)
            image = cv2.imdecode(np.frombuffer(payload,dtype=np.uint8),cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError('Invalid inspection JPEG')
            start = time.perf_counter()
            raw_detections = detector.infer(image)
            latency = (time.perf_counter()-start)*1000
            roi = message.get('product_roi')
            if message.get('product_id') or message.get('inspection_id') or roi is not None:
                detections = product_detections(raw_detections, roi, image.shape)
            else:
                detections = raw_detections
            path.write_bytes(payload)
        except Exception:
            logging.exception('Inspection failed; product remains uninspected: %s', message.get('event_id'))
            continue
        result = {'event_id':message['event_id'],'timestamp':message['timestamp'],'image_path':str(path.relative_to(DATA)),
                  'detections':detections,'defective':bool(detections),'inference_ms':latency,
                  'raw_detections':raw_detections, 'product_roi': roi,
                  'input_frame_sha256': hashlib.sha256(payload).hexdigest(),
                  **inspection_identity(message), 'model_version': model_version}
        publish(client,'factory/quality',result)
        if detections and cam_slots.acquire(blocking=False):
            executor.submit(heatmap,image.copy(),result.copy())
        logging.info('Vision detections=%d CPU %.2fms',len(detections),latency)


if __name__ == '__main__':
    main()
