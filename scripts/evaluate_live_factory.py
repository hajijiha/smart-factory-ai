"""Evaluate a frozen detector on the first 100 frames of one declared live test run.

The script never trains, exports, promotes weights, or modifies acquired frames.
Whole-frame AP and target-product ROI AP are separate measurements. CPU timings
include preprocessing, detector inference and NMS, excluding JPEG decoding/CAM.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import platform
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path
from datetime import datetime

from factory.settings import CONFIG, DATA, ARTIFACTS, REPORTS, database_url
from factory.provenance import sha256_file, vision_export_current


SAMPLES = 100
THREADS = 4
AP_CONFIDENCE = .001
AP_NMS_IOU = .7


def safe_path(root, relative):
    """Resolve acquired evidence strictly beneath its declared dataset root."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f'Missing or unsafe acquired file: {relative}')
    return path


def validate_labels(rows):
    """Require finite YOLO boxes, supported classes and complete image bounds."""
    result = []
    for row in rows:
        if len(row) != 5 or any(type(item) not in (int, float) or not math.isfinite(item) for item in row):
            raise ValueError('Label must contain five finite numeric fields')
        kind, x, y, width, height = row
        if int(kind) != kind or int(kind) not in (0, 1, 2):
            raise ValueError('Unsupported defect class')
        if not (0 < width <= 1 and 0 < height <= 1 and
                -1e-7 <= x-width/2 <= x+width/2 <= 1+1e-7 and
                -1e-7 <= y-height/2 <= y+height/2 <= 1+1e-7):
            raise ValueError('Label is outside image bounds or has zero area')
        result.append([int(kind), float(x), float(y), float(width), float(height)])
    return result


def validate_capture_protocol(record):
    """Require bounded measured-state interpolation and acknowledged capture delivery."""
    alignment = record.get('pose_alignment') or {}
    if alignment.get('method') != 'linear_position_and_quaternion_slerp_between_measured_states' or alignment.get('approximate') is not True or alignment.get('extrapolated') is not False:
        raise ValueError('Frame reference lacks declared measured-state interpolation')
    fields = ('before_sim_time', 'after_sim_time', 'gap_sim_seconds', 'camera_sim_time', 'interpolation_ratio')
    if any(type(alignment.get(key)) not in (int, float) or not math.isfinite(alignment[key]) for key in fields):
        raise ValueError('Frame alignment metadata is nonfinite or absent')
    before, after, camera, gap = [alignment[key] for key in ('before_sim_time', 'after_sim_time', 'camera_sim_time', 'gap_sim_seconds')]
    if not (0 < gap <= .10+1e-9 and before <= camera <= after and
            math.isclose(after-before, gap, abs_tol=1e-9, rel_tol=0) and 0 <= alignment['interpolation_ratio'] <= 1 and
            math.isclose(alignment['interpolation_ratio'], (camera-before)/gap, abs_tol=1e-8, rel_tol=0)):
        raise ValueError('Frame alignment exceeds observed bounds or uses extrapolation')
    if not math.isclose(camera, record['camera_sim_time'], abs_tol=1e-9, rel_tol=0):
        raise ValueError('Aligned pose belongs to another camera frame')
    if any(type(alignment.get(key)) is not int for key in ('before_sequence', 'after_sequence')) or alignment['after_sequence'] <= alignment['before_sequence']:
        raise ValueError('Frame alignment is not between ordered observed sequences')
    age, sensor, delay = [record.get(key) for key in ('camera_receipt_age_seconds_at_capture', 'sensor_sim_time_offset_seconds', 'delivery_delay_seconds')]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (age, sensor, delay)):
        raise ValueError('Capture freshness or acknowledgement delay is absent')
    if not (0 <= age <= 2 and abs(sensor) <= .15 and delay >= 0):
        raise ValueError('Capture camera/sensor freshness bounds were not met')
    try:
        captured = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))
        acknowledged = datetime.fromisoformat(record['delivery_acknowledged_at'].replace('Z', '+00:00'))
        if captured.tzinfo is None or acknowledged.tzinfo is None or acknowledged < captured:
            raise ValueError('Capture delivery acknowledgement predates capture')
    except (KeyError, TypeError, AttributeError) as failure:
        raise ValueError('Capture requires an aware delivery acknowledgement time') from failure
    return alignment


def select_frames(root, run_id):
    """Lock the first 100 unique frames of the requested balanced run before inference."""
    import cv2
    import numpy as np
    from factory.vision.service import product_detections
    manifest = (root / 'manifest.jsonl').read_bytes()
    selected, seen = [], {name: set() for name in ('event_id', 'product_id', 'inspection_id')}
    for line_number, line in enumerate(manifest.splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get('run_id') != run_id or record.get('defect_mode') != 'balanced-evaluation':
            continue
        validate_capture_protocol(record)
        if selected and record['camera_sim_time'] <= selected[-1]['record']['camera_sim_time']:
            raise ValueError('Selected camera frames repeat or go backwards in simulation time')
        for name in seen:
            value = record.get(name)
            if not isinstance(value, str) or not value or value in seen[name]:
                raise ValueError(f'Missing or duplicate {name} in selected run')
            seen[name].add(value)
        image_path, label_path = safe_path(root, record['image']), safe_path(root, record['label'])
        if sha256_file(image_path) != record['sha256']:
            raise ValueError('Acquired frame hash differs from its private reference')
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('Unreadable acquired JPEG')
        labels = validate_labels([list(map(float, row.split())) for row in label_path.read_text().splitlines() if row.strip()])
        reference = validate_labels(record['image_labels'])
        if len(labels) != len(reference) or not np.allclose(np.asarray(labels), np.asarray(reference), atol=1e-8, rtol=0):
            raise ValueError('YOLO labels differ from private observed geometry')
        target = validate_labels(record['target_product_labels'])
        kind = record['ground_truth_class']
        if type(kind) is not int or kind not in (-1, 0, 1, 2) or (len(target) != (0 if kind == -1 else 1)):
            raise ValueError('Focused product reference is inconsistent')
        if target and target[0][0] != kind:
            raise ValueError('Focused product label belongs to another class')
        product_detections([], record['product_roi'], image.shape)
        if record.get('target_product_roi') != record['product_roi']:
            raise ValueError('Target plate bounds differ between reference fields')
        selected.append({'record': record, 'manifest_line': line_number, 'image_path': image_path,
                         'label_path': label_path, 'labels': labels, 'target_labels': target,
                         'label_sha256': sha256_file(label_path), 'image': image})
        if len(selected) == SAMPLES:
            break
    if len(selected) != SAMPLES:
        raise RuntimeError(f'Run {run_id} has only {len(selected)} valid balanced frames; require first {SAMPLES}')
    counts = Counter(item['record']['ground_truth_class'] for item in selected)
    if any(counts[kind] == 0 for kind in (-1, 0, 1, 2)):
        raise ValueError('The fixed sample lacks a required normal/defect class')
    return selected, hashlib.sha256(manifest).hexdigest()


def verify_ai_evidence(selected, expected_hash):
    """Verify identical input bytes, deployed model hash and the full actual AI event join."""
    import psycopg
    from psycopg.rows import dict_row
    identifiers = [item['record']['event_id'] for item in selected]
    deadline = time.monotonic() + 30
    while True:
        with psycopg.connect(database_url(), row_factory=dict_row) as db:
            rows = db.execute('SELECT DISTINCT event_id,data FROM quality_inspections WHERE event_id=ANY(%s)',
                              (identifiers,)).fetchall()
        evidence = {row['event_id']: row['data'] for row in rows}
        if len(evidence) == SAMPLES:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Actual AI evidence missing for {SAMPLES-len(evidence)} selected frames')
        time.sleep(.5)
    for item in selected:
        record = item['record']
        quality = evidence[record['event_id']]
        for key in ('run_id', 'product_id', 'inspection_id', 'camera_timestamp', 'timestamp'):
            if quality.get(key) != record.get(key) or not record.get(key):
                raise ValueError(f'Actual AI evidence mismatch: {key}')
        if quality.get('sensor_event_id') != record['event_id']:
            raise ValueError('Actual sensor and camera event identities differ')
        if quality.get('model_version') != 'sha256:' + expected_hash:
            raise ValueError('Actual deployed model differs from the frozen checkpoint')
        if not math.isclose(quality['camera_sim_time'], record['camera_sim_time'], abs_tol=1e-8, rel_tol=0):
            raise ValueError('AI evidence belongs to a different camera frame')
        if quality.get('product_roi') != record['product_roi']:
            raise ValueError('Deployed and independently observed plate bounds differ')
        for key in ('pose_alignment', 'camera_receipt_age_seconds_at_capture', 'sensor_sim_time_offset_seconds'):
            if quality.get(key) != record[key]:
                raise ValueError(f'AI capture alignment metadata mismatch: {key}')
        if quality.get('input_frame_sha256') != record['sha256']:
            raise ValueError('AI did not inspect the exact acquired image bytes')
        if sha256_file(safe_path(DATA, quality['image_path'])) != record['sha256']:
            raise ValueError('Actual AI JPEG evidence differs from its input frame')
        item['quality'] = quality
    return evidence


def label_boxes(labels, shape):
    """Convert normalized reference boxes into the camera's observed pixel dimensions."""
    height, width = shape[:2]
    return [{'class_id': int(kind), 'bbox': [(x-w/2)*width, (y-h/2)*height, (x+w/2)*width, (y+h/2)*height]}
            for kind, x, y, w, h in labels]


def overlap(first, second):
    """Compute independent box IoU without consulting detector internals."""
    x1, y1, x2, y2 = max(first[0], second[0]), max(first[1], second[1]), min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0, x2-x1)*max(0, y2-y1)
    area = max(0, first[2]-first[0])*max(0, first[3]-first[1]) + max(0, second[2]-second[0])*max(0, second[3]-second[1])
    return intersection / max(area-intersection, 1e-12)


def match_predictions(predictions, labels, thresholds):
    """Assign each reference and prediction at most once, ordered by confidence."""
    import numpy as np
    ordered = sorted(predictions, key=lambda item: item['confidence'], reverse=True)
    correct = np.zeros((len(ordered), len(thresholds)), dtype=bool)
    for column, threshold in enumerate(thresholds):
        used = set()
        for index, prediction in enumerate(ordered):
            candidates = [(overlap(prediction['bbox'], target['bbox']), label_index)
                          for label_index, target in enumerate(labels)
                          if label_index not in used and prediction['class_id'] == target['class_id']]
            if candidates:
                score, label_index = max(candidates)
                if score >= threshold:
                    correct[index, column] = True
                    used.add(label_index)
    return ordered, correct


def detection_metrics(selected, predictions, target_only=False):
    """Use official Ultralytics AP integration on independently matched reference boxes."""
    import numpy as np
    from ultralytics.utils.metrics import ap_per_class
    matches, confidences, classes, targets = [], [], [], []
    for item, predicted in zip(selected, predictions):
        labels = label_boxes(item['target_labels'] if target_only else item['labels'], item['image'].shape)
        ordered, correct = match_predictions(predicted, labels, np.arange(.5, 1, .05))
        matches.append(correct)
        confidences.extend(prediction['confidence'] for prediction in ordered)
        classes.extend(prediction['class_id'] for prediction in ordered)
        targets.extend(label['class_id'] for label in labels)
    metrics = ap_per_class(np.concatenate(matches), np.asarray(confidences), np.asarray(classes), np.asarray(targets),
                           plot=False, names=dict(enumerate(CONFIG['vision']['classes'])))
    ap, indices = metrics[5], metrics[6]
    per_class = {name: {'AP50': 0.0, 'AP50_95': 0.0, 'labels': targets.count(index)}
                 for index, name in enumerate(CONFIG['vision']['classes'])}
    for index, class_index in enumerate(indices):
        per_class[CONFIG['vision']['classes'][int(class_index)]].update(AP50=float(ap[index, 0]), AP50_95=float(ap[index].mean()))
    return {'mAP50': sum(item['AP50'] for item in per_class.values())/3,
            'mAP50_95': sum(item['AP50_95'] for item in per_class.values())/3, 'classes': per_class,
            'confidence_floor': AP_CONFIDENCE, 'nms_iou': AP_NMS_IOU,
            'method': 'Confidence-ordered one-to-one class/IoU matches; official Ultralytics AP integration'}


def operating_metrics(selected, predictions, target_only=True):
    """Report the frozen operational threshold and an explicit normal-product denominator."""
    from scripts.bias_metrics import binary_metrics
    true_presence, predicted_presence = [], []
    tp = fp = fn = 0
    for item, predicted in zip(selected, predictions):
        reference = item['target_labels'] if target_only else item['labels']
        labels = label_boxes(reference, item['image'].shape)
        _, correct = match_predictions(predicted, labels, [.5])
        matched = int(correct.sum())
        tp += matched
        fp += len(predicted)-matched
        fn += len(labels)-matched
        true_presence.append(bool(labels))
        predicted_presence.append(bool(predicted))
    return {'image_classification': binary_metrics(true_presence, predicted_presence),
            'detection_iou50': {'tp': tp, 'fp': fp, 'fn': fn,
                                'precision': tp/(tp+fp) if tp+fp else 0,
                                'recall': tp/(tp+fn) if tp+fn else None,
                                'f1': 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0},
            'normal_definition': 'Selected normal product' if target_only else 'Whole camera image with no visible reference defect',
            'confidence': CONFIG['vision']['confidence'], 'nms_iou': CONFIG['vision']['iou']}


def pt_detections(model, image, confidence, nms_iou):
    """Run the frozen CPU PT detector and serialize its actual post-NMS boxes."""
    result = model.predict(image, imgsz=CONFIG['vision']['image_size'], conf=confidence,
                           iou=nms_iou, device='cpu', verbose=False, agnostic_nms=False)[0]
    return [{'class_id': int(kind), 'confidence': float(score), 'bbox': box.tolist()}
            for box, kind, score in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.cls.cpu().numpy(), result.boxes.conf.cpu().numpy())]


def timing_summary(values):
    """Summarize measured serial end-to-end detector timings, including the tail."""
    import numpy as np
    return {'images': len(values), 'mean_ms': float(np.mean(values)), 'p50_ms': float(np.percentile(values, 50)),
            'p95_ms': float(np.percentile(values, 95)), 'fps': 1000/float(np.mean(values)), 'times_ms': values}


def benchmark_frames(model, detector, selected, confidence, nms_iou):
    """Use the same ten warmups and 100 serial detector calls under each workload."""
    import torch
    for item in selected[:10]:
        detector.infer(item['image'])
        pt_detections(model, item['image'], confidence, nms_iou)
        torch.set_num_threads(THREADS)
    times_pt, times_onnx, operating_pt, operating_onnx = [], [], [], []
    for item in selected:
        start = time.perf_counter()
        predicted = detector.infer(item['image'])
        times_onnx.append((time.perf_counter()-start)*1000)
        operating_onnx.append(predicted)
        start = time.perf_counter()
        predicted = pt_detections(model, item['image'], confidence, nms_iou)
        times_pt.append((time.perf_counter()-start)*1000)
        operating_pt.append(predicted)
    return times_pt, times_onnx, operating_pt, operating_onnx


def timing_only(args):
    """Benchmark the previously locked sample without rerunning or replacing accuracy."""
    import cv2
    import torch
    from ultralytics import YOLO
    from factory.vision.inference import VisionDetector
    selection_path = args.selection_manifest.resolve()
    output = args.output_dir.resolve()
    if output == selection_path.parent:
        raise ValueError('Timing-only output must differ from the original accuracy directory')
    locked = selection_path.read_bytes()
    records = [json.loads(line) for line in locked.splitlines() if line.strip()]
    original = json.loads((selection_path.parent/'new-world-vision.json').read_text())
    selection_hash = hashlib.sha256(locked).hexdigest()
    if len(records) != SAMPLES or original['selection_manifest_sha256'] != selection_hash:
        raise ValueError('Previously evaluated first-100 selection changed')
    hashes = {name: sha256_file(ARTIFACTS/name) for name in ('vision.pt', 'vision.onnx')}
    if hashes != original['model_hashes'] or hashes['vision.pt'] != args.expected_model_sha256 or not vision_export_current(ARTIFACTS):
        raise ValueError('Standalone benchmark model differs from the loaded-workload model')
    selected = []
    for record in records:
        if record['run_id'] != args.run_id or record['actual_model_version'] != 'sha256:' + hashes['vision.pt']:
            raise ValueError('Locked sample contains another run or model')
        path = safe_path(args.dataset, record['image'])
        if sha256_file(path) != record['sha256']:
            raise ValueError('Locked benchmark image changed')
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('Unreadable locked benchmark image')
        selected.append({'record': record, 'image': image})
    CONFIG['vision']['threads'] = THREADS
    cv2.setNumThreads(1)
    torch.set_num_threads(THREADS)
    torch.set_num_interop_threads(1)
    model, detector = YOLO(str(ARTIFACTS/'vision.pt')), VisionDetector(ARTIFACTS/'vision.onnx')
    times_pt, times_onnx, _, _ = benchmark_frames(model, detector, selected, CONFIG['vision']['confidence'], CONFIG['vision']['iou'])
    report = {'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'run_id': args.run_id,
              'accuracy_repeated': False, 'selection_manifest_sha256': selection_hash, 'model_hashes': hashes,
              'workload': args.workload_label, 'device': 'CPU', 'threads': THREADS,
              'actual_torch_threads': torch.get_num_threads(), 'warmup_images': 10, 'images': SAMPLES, 'batch_size': 1,
              'scope': original['benchmark']['scope'], 'onnx': timing_summary(times_onnx), 'pytorch': timing_summary(times_pt)}
    if any(sha256_file(ARTIFACTS/name) != digest for name, digest in hashes.items()):
        raise RuntimeError('Frozen model changed during standalone benchmark')
    output.mkdir(parents=True, exist_ok=True)
    (output/'new-world-timing.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    with (output/'new-world-latency.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['index', 'event_id', 'image_sha256', 'onnx_ms', 'pytorch_ms'])
        for index, (item, onnx_time, pt_time) in enumerate(zip(selected, times_onnx, times_pt)):
            writer.writerow([index, item['record']['event_id'], item['record']['sha256'], onnx_time, pt_time])
    print(json.dumps({key: value for key, value in report.items() if key not in ('onnx', 'pytorch')}, indent=2))
    print(json.dumps({name: {key: value for key, value in report[name].items() if key != 'times_ms'} for name in ('onnx', 'pytorch')}, indent=2))


def evaluate(args):
    """Validate evidence first, evaluate the held-out run once, then write isolated reports."""
    import cv2
    import numpy as np
    import torch
    from ultralytics import YOLO
    from factory.vision.inference import VisionDetector
    from factory.vision.service import product_detections
    weight, graph = ARTIFACTS/'vision.pt', ARTIFACTS/'vision.onnx'
    if sha256_file(weight) != args.expected_model_sha256 or not (ARTIFACTS/'vision.export.json').is_file() or not vision_export_current(ARTIFACTS):
        raise ValueError('Frozen checkpoint or its ONNX export identity is inconsistent')
    hashes = {'vision.pt': sha256_file(weight), 'vision.onnx': sha256_file(graph)}
    selected, manifest_hash = select_frames(args.dataset, args.run_id)
    verify_ai_evidence(selected, hashes['vision.pt'])
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    selection = [{**item['record'], 'label_sha256': item['label_sha256'], 'manifest_line': item['manifest_line'],
                  'actual_model_version': item['quality']['model_version'], 'actual_input_frame_sha256': item['quality']['input_frame_sha256']}
                 for item in selected]
    locked = ''.join(json.dumps(record, sort_keys=True, allow_nan=False)+'\n' for record in selection)
    (output/'new-world-selection.jsonl').write_text(locked, encoding='utf-8')
    if args.validate_only:
        print(json.dumps({'validated_frames': SAMPLES, 'run_id': args.run_id, 'model_sha256': hashes['vision.pt']}))
        return
    CONFIG['vision']['threads'] = THREADS
    cv2.setNumThreads(1)
    torch.set_num_threads(THREADS)
    torch.set_num_interop_threads(1)
    model = YOLO(str(weight))
    with tempfile.TemporaryDirectory(prefix='live-evaluation-', dir=output) as directory:
        root = Path(directory)
        (root/'images/test').mkdir(parents=True)
        (root/'labels/test').mkdir(parents=True)
        for index, item in enumerate(selected):
            name = f'{index:03d}-{item["record"]["event_id"]}'
            shutil.copy2(item['image_path'], root/'images/test'/f'{name}.jpg')
            shutil.copy2(item['label_path'], root/'labels/test'/f'{name}.txt')
        recipe = {'path': str(root), 'train': 'images/test', 'val': 'images/test', 'test': 'images/test',
                  'names': CONFIG['vision']['classes']}
        (root/'dataset.yaml').write_text(json.dumps(recipe), encoding='utf-8')
        measured = model.val(data=str(root/'dataset.yaml'), split='test', imgsz=CONFIG['vision']['image_size'],
                             device='cpu', batch=16, workers=0, conf=AP_CONFIDENCE, iou=AP_NMS_IOU,
                             plots=True, project=str(output/'ultralytics'), name='new-world-first100', exist_ok=True)
        raw_pt = {'mAP50': float(measured.box.map50), 'mAP50_95': float(measured.box.map),
                  'class_AP50': dict(zip(CONFIG['vision']['classes'], measured.box.ap50.tolist())),
                  'method': 'Ultralytics frozen PT model.val(split=test) on all visible defect labels'}
    detector = VisionDetector(graph)
    confidence, nms_iou = CONFIG['vision']['confidence'], CONFIG['vision']['iou']
    low_pt, low_onnx = [], []
    try:
        CONFIG['vision']['confidence'], CONFIG['vision']['iou'] = AP_CONFIDENCE, AP_NMS_IOU
        for item in selected:
            low_pt.append(pt_detections(model, item['image'], AP_CONFIDENCE, AP_NMS_IOU))
            low_onnx.append(detector.infer(item['image']))
    finally:
        CONFIG['vision']['confidence'], CONFIG['vision']['iou'] = confidence, nms_iou
    roi_pt = [product_detections(predicted, item['record']['product_roi'], item['image'].shape) for item, predicted in zip(selected, low_pt)]
    roi_onnx = [product_detections(predicted, item['record']['product_roi'], item['image'].shape) for item, predicted in zip(selected, low_onnx)]
    times_pt, times_onnx, operating_pt, operating_onnx = benchmark_frames(model, detector, selected, confidence, nms_iou)
    filtered_pt = [product_detections(predicted, item['record']['product_roi'], item['image'].shape) for item, predicted in zip(selected, operating_pt)]
    filtered_onnx = [product_detections(predicted, item['record']['product_roi'], item['image'].shape) for item, predicted in zip(selected, operating_onnx)]
    report = {'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'run_id': args.run_id,
              'model_status': 'Frozen original baseline; no training, export, threshold fitting or promotion',
              'frames': SAMPLES, 'selection': 'First 100 unique records of the specified balanced-evaluation run in manifest order',
              'source_manifest_sha256': manifest_hash, 'selection_manifest_sha256': hashlib.sha256(locked.encode()).hexdigest(),
              'model_hashes': hashes, 'actual_ai_frame_and_model_evidence_verified': True,
              'onnx_export_binding_verified': True,
              'camera_pose_alignment': {'approximate': True, 'extrapolated': False,
                                        'aligned_pose_time_note': 'Pose timestamp is assigned to camera time by bounded interpolation; it is not a direct camera-time pose observation.',
                                        'maximum_observation_gap_sim_seconds': max(item['record']['pose_alignment']['gap_sim_seconds'] for item in selected),
                                        'p95_observation_gap_sim_seconds': float(np.percentile([item['record']['pose_alignment']['gap_sim_seconds'] for item in selected], 95)),
                                        'maximum_camera_receipt_age_seconds_at_capture': max(item['record']['camera_receipt_age_seconds_at_capture'] for item in selected),
                                        'p95_camera_receipt_age_seconds_at_capture': float(np.percentile([item['record']['camera_receipt_age_seconds_at_capture'] for item in selected], 95)),
                                        'maximum_abs_sensor_sim_time_offset_seconds': max(abs(item['record']['sensor_sim_time_offset_seconds']) for item in selected),
                                        'p95_abs_sensor_sim_time_offset_seconds': float(np.percentile([abs(item['record']['sensor_sim_time_offset_seconds']) for item in selected], 95)),
                                        'maximum_delivery_delay_seconds': max(item['record']['delivery_delay_seconds'] for item in selected)},
              'focused_class_counts': dict(Counter(str(item['record']['ground_truth_class']) for item in selected)),
              'visible_label_counts': dict(Counter(str(label[0]) for item in selected for label in item['labels'])),
              'focused_normal_frames_with_visible_neighbor_defects': sum(item['record']['ground_truth_class'] == -1 and bool(item['labels']) for item in selected),
              'raw_whole_frame': {'pytorch_official': raw_pt, 'onnx': detection_metrics(selected, low_onnx),
                                  'onnx_operating_point': operating_metrics(selected, operating_onnx, False)},
              'target_product_roi': {'pytorch': detection_metrics(selected, roi_pt, True),
                                     'onnx': detection_metrics(selected, roi_onnx, True),
                                     'pytorch_operating_point': operating_metrics(selected, filtered_pt),
                                     'onnx_operating_point': operating_metrics(selected, filtered_onnx),
                                     'rule': 'Detection bbox center within plate bounds projected from observed pose; reference labels never used to define ROI'},
              'benchmark': {'device': 'CPU', 'threads': THREADS, 'actual_torch_threads': torch.get_num_threads(),
                            'warmup_images': 10, 'batch_size': 1, 'images': SAMPLES,
                            'scope': 'Resize, BGR/RGB, normalization, inference and NMS; excludes JPEG decoding, DB, ROI filter and Grad-CAM',
                            'workload': args.workload_label, 'onnx': timing_summary(times_onnx), 'pytorch': timing_summary(times_pt)},
              'hardware': {'logical_cpus': os.cpu_count(), 'python': platform.python_version(), 'torch': torch.__version__},
              'limitations': ['Synthetic balanced test; not a real-factory accuracy claim or production defect prevalence estimate.',
                             'One run and camera/lighting condition; trajectory/product IDs are distinct, physical slots are reused.',
                             'Reference labels use approximate interpolation between measured states at most 0.10 simulation seconds apart; they are not manual pixel masks.',
                             'Whole-frame and selected-product metrics use different references and must be read separately.']}
    if any(sha256_file(ARTIFACTS/name) != digest for name, digest in hashes.items()):
        raise RuntimeError('Frozen model artifacts changed during evaluation')
    report['gates'] = {'raw_whole_frame_mAP50_ge_0_80': raw_pt['mAP50'] >= .8,
                       'target_roi_onnx_mAP50_ge_0_80': report['target_product_roi']['onnx']['mAP50'] >= .8,
                       'onnx_100_images_fps_ge_20': report['benchmark']['onnx']['fps'] >= 20}
    (output/'new-world-vision.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    with (output/'new-world-latency.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['index', 'event_id', 'product_id', 'inspection_id', 'class_id', 'image_sha256', 'label_sha256', 'onnx_ms', 'pytorch_ms'])
        for index, (item, onnx_time, pt_time) in enumerate(zip(selected, times_onnx, times_pt)):
            writer.writerow([index, item['record']['event_id'], item['record']['product_id'], item['record']['inspection_id'],
                             item['record']['ground_truth_class'], item['record']['sha256'], item['label_sha256'], onnx_time, pt_time])
    plot_results(output, report, times_onnx, times_pt)
    print(json.dumps({key: value for key, value in report.items() if key != 'benchmark'}, indent=2), flush=True)
    print(json.dumps({name: {key: value for key, value in report['benchmark'][name].items() if key != 'times_ms'}
                      for name in ('onnx', 'pytorch')}, indent=2), flush=True)


def plot_results(output, report, onnx_times, pt_times):
    """Save standard static class-AP and measured latency plots for the isolated report."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    names = CONFIG['vision']['classes']
    figure, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    positions = np.arange(3)
    raw = report['raw_whole_frame']['pytorch_official']['class_AP50']
    roi = report['target_product_roi']['onnx']['classes']
    axes[0].bar(positions-.18, [raw[name] for name in names], .36, label='Raw whole frame PT')
    axes[0].bar(positions+.18, [roi[name]['AP50'] for name in names], .36, label='Target ROI ONNX')
    axes[0].set_xticks(positions, names)
    axes[0].set_ylim(0, 1.05)
    axes[0].set_ylabel('AP50')
    axes[0].axhline(.8, color='gray', linestyle='--', linewidth=1)
    axes[0].legend(fontsize=8)
    axes[0].set_title('Frozen baseline: distinct evaluation scopes')
    axes[1].plot(onnx_times, label='ONNX', linewidth=1)
    axes[1].plot(pt_times, label='PyTorch', linewidth=1)
    axes[1].axhline(50, color='gray', linestyle='--', linewidth=1, label='20 FPS threshold')
    axes[1].set_xlabel('Fixed sample index')
    axes[1].set_ylabel('Detector end-to-end latency (ms)')
    axes[1].legend(fontsize=8)
    figure.savefig(output/'new-world-vision.png', dpi=180)
    plt.close(figure)


def main():
    """Require the run and pre-recorded immutable checkpoint identity explicitly."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--expected-model-sha256', required=True)
    parser.add_argument('--dataset', type=Path, default=DATA/'factory-v1-evaluation')
    parser.add_argument('--workload-label', default='Integrated factory services active')
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=REPORTS/'factory-web-v1')
    parser.add_argument('--timing-only', action='store_true')
    parser.add_argument('--selection-manifest', type=Path)
    args = parser.parse_args()
    if len(args.expected_model_sha256) != 64 or any(char not in '0123456789abcdef' for char in args.expected_model_sha256):
        parser.error('Expected checkpoint SHA256 must be the pre-recorded lowercase 64-character digest')
    if args.timing_only:
        if args.validate_only or args.selection_manifest is None:
            parser.error('Timing-only requires --selection-manifest and cannot also validate-only')
        timing_only(args)
    else:
        evaluate(args)


if __name__ == '__main__':
    main()
