"""Compare final ONNX/PyTorch detections on the unchanged seed-42 held-out sample."""
import hashlib
import json
import cv2
import numpy as np
import torch
from scipy.optimize import linear_sum_assignment
from ultralytics import YOLO
from factory.common import CONFIG, DATA, ARTIFACTS
from factory.vision.inference import VisionDetector


def iou(first, second):
    """Compute pixel-coordinate box overlap independently of either inference engine."""
    low = np.maximum(first[:2],second[:2])
    high = np.minimum(first[2:],second[2:])
    intersection = float(np.maximum(high-low,0).prod())
    areas = float(np.maximum(first[2:]-first[:2],0).prod()+np.maximum(second[2:]-second[:2],0).prod())
    return intersection/max(areas-intersection, 1e-12)


def compare():
    """Check prediction equivalence without consulting labels, tuning or writing artifacts."""
    files = sorted((DATA/'vision/images/test').glob('*.jpg'))
    if len(files) < 100:
        raise RuntimeError('Generate the Gazebo dataset before the 100-image parity check')
    weight, graph = ARTIFACTS/'vision.pt', ARTIFACTS/'vision.onnx'
    if not weight.exists() or not graph.exists():
        raise RuntimeError('Train/evaluate or start the vision service to prepare PT and ONNX artifacts')
    torch.set_num_threads(CONFIG['vision']['threads'])
    model, detector = YOLO(str(weight)), VisionDetector(graph)
    chosen = np.random.default_rng(42).choice(files, size=100, replace=False)
    images = [cv2.imread(str(path)) for path in chosen]
    kwargs = {'imgsz':CONFIG['vision']['image_size'],'conf':CONFIG['vision']['confidence'],
              'iou':CONFIG['vision']['iou'],'device':'cpu','verbose':False,'agnostic_nms':False}
    for image in images[:10]:
        detector.infer(image)
        model.predict(image, **kwargs)
    torch.set_num_threads(CONFIG['vision']['threads'])
    max_box_delta, max_conf_delta, min_iou = 0.0, 0.0, 1.0
    matched, normal_agreement = 0, 0
    count_mismatches, class_mismatches, numeric_mismatches = [], [], []
    for path,image in zip(chosen,images):
        onnx = detector.infer(image)
        result = model.predict(image, **kwargs)[0]
        pt_boxes = result.boxes.xyxy.cpu().numpy()
        pt_classes = result.boxes.cls.cpu().numpy().astype(int)
        pt_conf = result.boxes.conf.cpu().numpy()
        if len(onnx) != len(pt_boxes):
            count_mismatches.append({'file':path.name,'onnx':len(onnx),'pytorch':len(pt_boxes)})
            continue
        if not onnx:
            normal_agreement += 1
            continue
        overlaps = np.asarray([[iou(np.asarray(item['bbox']),box) if item['class_id']==class_id else -1
                               for box,class_id in zip(pt_boxes,pt_classes)] for item in onnx])
        first,second = linear_sum_assignment(-overlaps)
        for a,b in zip(first,second):
            item = onnx[a]
            if item['class_id'] != int(pt_classes[b]):
                class_mismatches.append(path.name)
                continue
            matched += 1
            overlap = float(overlaps[a,b])
            delta = float(np.max(np.abs(np.asarray(item['bbox'])-pt_boxes[b])))
            confidence_delta = abs(item['confidence']-float(pt_conf[b]))
            min_iou = min(min_iou,overlap)
            max_box_delta = max(max_box_delta,delta)
            max_conf_delta = max(max_conf_delta,confidence_delta)
            if overlap < .99 or delta > .01 or confidence_delta > .0001:
                numeric_mismatches.append({'file':path.name,'iou':overlap,'pixel_delta':delta,'confidence_delta':confidence_delta})
    return {
        'images':100,'selection_seed':42,'split':'test','threads':CONFIG['vision']['threads'],
        'confidence_threshold':kwargs['conf'],'nms_iou_threshold':kwargs['iou'],
        'matched_detections':matched,'no_detection_agreement_images':normal_agreement,
        'count_mismatches':count_mismatches,'class_mismatches':class_mismatches,'numeric_mismatches':numeric_mismatches,
        'min_matched_box_iou':min_iou,'max_absolute_box_delta_pixels':max_box_delta,
        'max_absolute_confidence_delta':max_conf_delta,
        'vision_pt_sha256':hashlib.sha256(weight.read_bytes()).hexdigest(),
        'vision_onnx_sha256':hashlib.sha256(graph.read_bytes()).hexdigest(),
        'selected_images':[path.name for path in chosen],
        'limits':'Prediction equivalence only; labels not consulted, no retraining or threshold tuning; no new latency claims while live services run.',
        'passed':not (count_mismatches or class_mismatches or numeric_mismatches),
    }


if __name__ == '__main__':
    result = compare()
    print(json.dumps(result,indent=2))
    if not result['passed']:
        raise SystemExit(1)
