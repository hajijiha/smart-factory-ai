"""Frozen-model evaluation on an independently written synthetic challenge.

No fitting, threshold selection, ONNX export, training-file overwrite or service
command is performed here. Vision transforms are stress tests, not a new renderer.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
import platform
from importlib.metadata import version
from pathlib import Path

import numpy as np

from scripts.bias_metrics import (PROTOCOL, binary_metrics, iou, load_protocol,
                                  sha256, wilson, write_report)


def model_hashes(artifacts):
    """Bind each report to the weights/graph actually available at evaluation time."""
    return {name: sha256(Path(artifacts) / name) for name in ['pdm.pt', 'vision.pt', 'vision.onnx']
            if (Path(artifacts) / name).exists()}


def independent_wave(rotation_hz, level, noise, seed, fault_type='mixed', samples=2048):
    """Alternative signal construction, deliberately independent of simulator.signal.

    Load/gain/DC/background noise are sampled independently of severity. Faults
    affect harmonics or bearing-envelope impulses; broadband noise is not a label.
    These equations are a challenge model, not measured bearing-failure physics.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(samples) / 2048
    phase, gain, load = rng.uniform(0, 2 * np.pi), rng.uniform(.75, 1.25), rng.uniform(.7, 1.3)
    dc = rng.uniform(-.015, .015)
    baseline = .16 * (rotation_hz / 25) ** 2 * load * np.sin(2 * np.pi * rotation_hz * t + phase)
    # Small normal harmonics are nuisance, present at every fault level.
    baseline += rng.uniform(.003, .015) * np.sin(4 * np.pi * rotation_hz * t + phase / 2)
    severity = level / 10
    fault = np.zeros(samples)
    if fault_type in ['imbalance', 'mixed']:
        fault += .55 * severity * np.sin(2 * np.pi * rotation_hz * t + phase)
    if fault_type in ['looseness', 'mixed']:
        fault += .35 * severity ** 1.25 * np.sin(4 * np.pi * rotation_hz * t + phase)
        fault += .20 * severity * np.sin(6 * np.pi * rotation_hz * t + phase / 3)
    # The frequency ratio agrees with the declared bearing geometry; the pulse
    # envelope/carrier are independently constructed and not copied from training.
    ratio = 7.94 / 39.0
    for kind, frequency in [('outer_race', 4 * rotation_hz * (1 - ratio)),
                            ('inner_race', 4 * rotation_hz * (1 + ratio))]:
        if fault_type not in [kind, 'mixed']:
            continue
        carrier = rng.uniform(220, 500)
        pulse_width = rng.uniform(.0015, .003)
        first = rng.uniform(0, 1 / frequency)
        for at in np.arange(first, 1, 1 / frequency):
            delta = t - at
            envelope = np.exp(-np.maximum(delta, 0) / pulse_width) * (delta >= 0)
            fault += .75 * severity * envelope * np.cos(2 * np.pi * carrier * delta)
    white = rng.normal(0, noise, samples)
    slow = rng.uniform(0, .018) * np.sin(2 * np.pi * rng.uniform(1, 4) * t + phase)
    return gain * (baseline + fault) + white + slow + dc


def evaluate_sensor(artifacts, protocol, seed):
    """Evaluate all cells and normal-noise controls with unchanged saved thresholds."""
    from factory.pdm.features import extract
    from factory.pdm.model import Detector
    detector = Detector(Path(artifacts) / 'pdm.pt')
    spec = protocol['sensor']
    conditions = [('supported', rpm, noise) for rpm in spec['supported_rotation_hz'] for noise in spec['supported_noise_std']]
    conditions += [('ood', rpm, noise) for rpm in spec['out_of_domain_rotation_hz'] for noise in spec['out_of_domain_noise_std']]
    vectors, metadata = [], []
    for index, (domain, rotation, noise) in enumerate(conditions):
        for level in spec['levels']:
            for sample in range(spec['samples_per_cell']):
                kind = spec['fault_types'][sample % len(spec['fault_types'])]
                # Same nuisance seed for the matched level sequence. Only the fault
                # component changes; noise and gain must not convey the label.
                sample_seed = seed + index * 10000 + sample
                wave = independent_wave(rotation, level, noise, sample_seed, kind)
                features, _ = extract(wave, rotation_hz=rotation)
                vectors.append(features)
                metadata.append({'domain': domain, 'rotation_hz': rotation, 'noise': noise,
                                 'level': level, 'fault_type': kind, 'seed': sample_seed})
    features = np.asarray(vectors)
    errors = dict(zip(['chart', 'ae'], detector.errors(features)))
    report = {'n': len(features), 'thresholds': dict(detector.thresholds), 'models': {},
              'training_dataset_identity': getattr(detector, 'dataset_identity', None),
              'evaluation_relationship': 'Intentional cross-generator challenge; fitted training identity is retained, not assumed to match challenge inputs',
              'construction': 'Independent equations; severity-independent background noise and matched nuisance seeds',
              'feature_sha256': hashlib.sha256(features.tobytes()).hexdigest(), 'warnings': []}
    labels = np.asarray([row['level'] > 0 for row in metadata])
    for model, scores in errors.items():
        predictions = scores > detector.thresholds[model]
        aggregate = {}
        for domain in ['supported', 'ood']:
            selection = np.asarray([row['domain'] == domain for row in metadata])
            aggregate[domain] = binary_metrics(labels[selection], predictions[selection])
        buckets = defaultdict(list)
        for i, row in enumerate(metadata):
            buckets[(row['domain'], row['rotation_hz'], row['noise'], row['level'])].append(i)
        cells = []
        for (domain, rotation, noise, level), indices in buckets.items():
            cells.append({'domain': domain, 'rotation_hz': rotation, 'noise': noise, 'level': level,
                          **binary_metrics(labels[indices], predictions[indices])})
        types = {}
        for kind in spec['fault_types']:
            selection = np.asarray([row['domain'] == 'supported' and row['fault_type'] == kind for row in metadata])
            types[kind] = binary_metrics(labels[selection], predictions[selection])
        supported = aggregate['supported']
        if supported['f1'] < spec['supported_f1_min'] or supported['normal_fpr'] > spec['supported_normal_fpr_max']:
            report['warnings'].append({'model': model, 'code': 'supported_gate_not_met', 'metrics': supported})
        for cell in cells:
            if cell['domain'] == 'supported' and cell['level'] == 0 and cell['normal_fpr'] > spec['normal_noise_only_fpr_max']:
                report['warnings'].append({'model': model, 'code': 'normal_noise_control_false_alarm', 'cell': cell})
        for level in range(3, 11):
            selection = np.asarray([row['domain'] == 'supported' and row['level'] == level for row in metadata])
            result = binary_metrics(labels[selection], predictions[selection])
            if result['recall'] < spec['level_3_to_10_recall_min']:
                report['warnings'].append({'model': model, 'code': 'severity_recall_below_gate', 'level': level, 'metrics': result})
        report['models'][model] = {'aggregate': aggregate, 'cells': cells, 'fault_type_metrics': types}
    return report


def transformed(image, name, box=None):
    """Deterministic paired stress/negative-control image transformations."""
    import cv2
    if name == 'identity':
        return image.copy()
    if name == 'grayscale':
        return cv2.cvtColor(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    if name in ['darken', 'brighten']:
        return np.clip(image.astype(float) * (.65 if name == 'darken' else 1.25), 0, 255).astype(np.uint8)
    if name == 'blur':
        return cv2.GaussianBlur(image, (7, 7), 1.4)
    if name == 'jpeg':
        ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 35])
        if not ok:
            raise ValueError('JPEG transform failed')
        return cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if name == 'erase_defect' and box is not None:
        mask = np.zeros(image.shape[:2], np.uint8)
        x1, y1, x2, y2 = box
        mask[max(0, int(y1) - 4):min(image.shape[0], math.ceil(y2) + 4),
             max(0, int(x1) - 4):min(image.shape[1], math.ceil(x2) + 4)] = 255
        return cv2.inpaint(image, mask, 5, cv2.INPAINT_TELEA)
    raise ValueError(f'Unsupported transform {name}')


def evaluate_vision(data, artifacts, protocol, seed):
    """Report paired recall/FPR; never label these stress metrics as detector mAP."""
    import cv2
    from factory.vision.inference import VisionDetector
    graph = Path(artifacts) / 'vision.onnx'
    if not graph.exists():
        return {'status': 'UNVALIDATED', 'reason': 'No frozen vision.onnx; export separately before freezing, not during evaluation'}
    detector = VisionDetector(graph)
    root = Path(data) / 'vision'
    rows = [json.loads(line) for line in (root / 'manifest.jsonl').read_text().splitlines() if line]
    rng = np.random.default_rng(seed)
    selected = []
    for class_id in [-1, 0, 1, 2]:
        eligible = [row for row in rows if row['scene']['split'] == 'test' and row['scene']['class_id'] == class_id]
        if len(eligible) < protocol['vision']['images_per_class']:
            raise ValueError(f'Insufficient untouched test images for class {class_id}')
        indices = rng.choice(len(eligible), protocol['vision']['images_per_class'], replace=False)
        selected.extend(eligible[int(i)] for i in indices)
    raw = []
    for row in selected:
        path = root / row['image']
        if sha256(path) != row['sha256']:
            raise ValueError(f'Image changed: {row["image"]}')
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f'Unreadable image {path}')
        class_id = int(row['scene']['class_id'])
        label_path = root / 'labels/test' / (path.stem + '.txt')
        label_hash = sha256(label_path)
        if row.get('label_sha256') and label_hash != row['label_sha256']:
            raise ValueError(f'Label changed: {label_path}')
        label = label_path.read_text().split()
        box = None
        if class_id >= 0:
            _, x, y, w, h = map(float, label)
            height, width = image.shape[:2]
            box = [(x - w / 2) * width, (y - h / 2) * height, (x + w / 2) * width, (y + h / 2) * height]
        names = list(protocol['vision']['transforms']) + (['erase_defect'] if class_id >= 0 else [])
        for name in names:
            input_image = np.ascontiguousarray(transformed(image, name, box))
            transformed_hash = hashlib.sha256(input_image.tobytes()).hexdigest()
            detections = detector.infer(input_image)
            matched = any(detection['class_id'] == class_id and iou(detection['bbox'], box) >= protocol['vision']['match_iou']
                          for detection in detections) if box is not None and name != 'erase_defect' else False
            top = max(detections, key=lambda item: item['confidence']) if detections else None
            raw.append({'image': row['image'], 'sha256': row['sha256'], 'label_sha256': label_hash,
                        'transformed_sha256': transformed_hash, 'input_shape': list(input_image.shape),
                        'input_dtype': str(input_image.dtype), 'class_id': class_id,
                        'transform': name, 'detected': bool(detections), 'matched': matched,
                        'prediction_count': len(detections), 'top_class': None if top is None else top['class_id'],
                        'top_confidence': None if top is None else top['confidence']})
    summary = {}
    for name in list(protocol['vision']['transforms']) + ['erase_defect']:
        records = [row for row in raw if row['transform'] == name]
        positives = [row for row in records if row['class_id'] >= 0 and name != 'erase_defect']
        negatives = [row for row in records if row['class_id'] == -1 or name == 'erase_defect']
        matched, false_positive = sum(row['matched'] for row in positives), sum(row['detected'] for row in negatives)
        result = {'positive_images': len(positives), 'matched_images': matched,
                  'recall_iou50': matched / len(positives) if positives else None,
                  'recall_wilson95': wilson(matched, len(positives)),
                  'negative_images': len(negatives), 'false_positive_images': false_positive,
                  'normal_image_fpr': false_positive / len(negatives) if negatives else None,
                  'fpr_wilson95': wilson(false_positive, len(negatives)), 'class_recall': {}}
        for cls in [0, 1, 2]:
            examples = [row for row in positives if row['class_id'] == cls]
            hits = sum(row['matched'] for row in examples)
            result['class_recall'][str(cls)] = {'n': len(examples), 'matched': hits,
                                               'recall': hits / len(examples) if examples else None,
                                               'wilson95': wilson(hits, len(examples))}
        summary[name] = result
    baseline = summary['identity']
    original = {row['image']: row for row in raw if row['transform'] == 'identity'}
    warnings = []
    for name in protocol['vision']['transforms']:
        if name == 'identity':
            continue
        result = summary[name]
        paired = [row for row in raw if row['transform'] == name]
        class_changes = sum(row['top_class'] != original[row['image']]['top_class'] for row in paired)
        confidence_deltas = [row['top_confidence'] - original[row['image']]['top_confidence'] for row in paired
                             if row['top_confidence'] is not None and original[row['image']]['top_confidence'] is not None]
        drop = baseline['recall_iou50'] - result['recall_iou50']
        fpr_gap = result['normal_image_fpr'] - baseline['normal_image_fpr']
        result.update(paired_recall_drop=drop, paired_normal_fpr_gap=fpr_gap,
                      paired_top_class_changes=class_changes, paired_n=len(paired),
                      paired_confidence_delta_n=len(confidence_deltas),
                      paired_confidence_delta_mean=float(np.mean(confidence_deltas)) if confidence_deltas else None)
        if drop > protocol['vision']['supported_recall_drop_warn'] or fpr_gap > protocol['vision']['normal_fpr_gap_warn']:
            warnings.append({'code': 'paired_stress_sensitivity', 'transform': name, 'recall_drop': drop, 'fpr_gap': fpr_gap})
    if summary['erase_defect']['normal_image_fpr'] > protocol['vision']['erased_defect_fpr_warn']:
        warnings.append({'code': 'erased_defect_detection', 'fpr': summary['erase_defect']['normal_image_fpr']})
    from factory.common import CONFIG
    return {'status': 'WARN' if warnings else 'PASS_SYNTHETIC_STRESS_ONLY', 'n_selected': len(selected),
            'manifest_sha256': sha256(root / 'manifest.jsonl'), 'summary': summary, 'raw': raw, 'warnings': warnings,
            'inference_config': {key: CONFIG['vision'][key] for key in ('image_size', 'confidence', 'iou', 'threads', 'classes')},
            'input_hash_scope': 'Decoded transformed contiguous BGR pixels before detector resize/normalization; shape and dtype recorded separately',
            'interpretation': 'Reused test imagery with postprocessing; no independent geometry/renderer or real-image validation. Inpainting introduces artifacts, so this is not a causal proof.'}


def main():
    """Verify frozen hashes before/after evaluation and emit a separate audit artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, default=PROTOCOL)
    parser.add_argument('--phase', choices=['development', 'final'], default='development')
    parser.add_argument('--freeze', type=Path)
    parser.add_argument('--freeze-out', type=Path)
    parser.add_argument('--sensor-only', action='store_true')
    parser.add_argument('--vision-only', action='store_true')
    args = parser.parse_args()
    if args.sensor_only and args.vision_only:
        parser.error('Choose at most one modality-only option')
    protocol, protocol_hash = load_protocol(args.protocol)
    before = model_hashes(args.artifacts)
    if args.freeze_out:
        write_report(args.freeze_out, {'models': before, 'protocol_sha256': protocol_hash,
                                      'rule': 'No tuning after final challenge outputs are observed'})
        return
    if args.phase == 'final' and not args.freeze:
        parser.error('Final evaluation requires a pre-evaluation --freeze record')
    if args.freeze:
        frozen = json.loads(args.freeze.read_text())
        if before != frozen['models'] or protocol_hash != frozen['protocol_sha256']:
            raise ValueError('Frozen model/protocol mismatch; final evaluation is invalid')
    report = {'phase': args.phase, 'seed': protocol['seeds'][args.phase],
              'protocol_id': protocol['protocol_id'], 'protocol_sha256': protocol_hash,
              'models': before, 'real_factory_validation': 'UNVALIDATED',
              'tuning_rule': protocol['registration'], 'findings': []}
    source_root = Path(__file__).resolve().parents[1]
    report['evaluation_source_sha256'] = {name: sha256(source_root / name) for name in (
        'scripts/bias_evaluate.py', 'scripts/bias_metrics.py', 'factory/pdm/features.py',
        'factory/pdm/model.py', 'factory/vision/inference.py', 'factory/common.py')}
    report['environment'] = {'python': platform.python_version(),
        **{name: version(name) for name in ('numpy', 'scipy', 'torch', 'opencv-python', 'onnxruntime')}}
    if not args.vision_only:
        report['sensor'] = evaluate_sensor(args.artifacts, protocol, report['seed'])
        report['findings'].extend(report['sensor']['warnings'])
    if not args.sensor_only:
        report['vision'] = evaluate_vision(args.data, args.artifacts, protocol, report['seed'])
        report['findings'].extend(report['vision'].get('warnings', []))
    if model_hashes(args.artifacts) != before:
        raise ValueError('Model artifacts changed during evaluation')
    report['status'] = 'WARN' if report['findings'] else ('PARTIAL_SYNTHETIC_CHECKS_ONLY'
        if report.get('vision', {}).get('status') == 'UNVALIDATED' else 'PASS_SYNTHETIC_CHECKS_ONLY')
    write_report(args.output, report)
    print(json.dumps({'phase': report['phase'], 'status': report['status'], 'findings': report['findings']}, indent=2))


if __name__ == '__main__':
    main()
