"""Estimate paired intervention effects from frozen models and actual 3D camera evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from factory.common import DATA, ARTIFACTS
from factory.settings import verification_path
from factory.pdm.model import Detector
from factory.vision.inference import VisionDetector


def paired_effect(values, seed=2026101206):
    """Bootstrap whole matched families rather than treating both arms as independent samples."""
    differences = np.asarray(values, dtype=float)
    if differences.ndim != 1 or not len(differences) or not np.isfinite(differences).all():
        raise ValueError('Paired effects require nonempty finite family differences')
    rng = np.random.default_rng(seed)
    draws = differences[rng.integers(0, len(differences), (5000, len(differences)))].mean(axis=1)
    return {'mean_difference': float(differences.mean()), 'bootstrap95': np.quantile(draws, [.025, .975]).tolist(), 'pairs': len(differences)}


def verify(root, output):
    """Require matched nuisance settings and report both generated and detected quality effects."""
    marker = json.loads((root / 'COMPLETE').read_text())
    for name, expected in marker['files'].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Intervention acquisition evidence changed')
    rows = json.loads((root / 'manifest.json').read_text())
    features = np.load(root / 'features.npz')['features']
    if len(rows) != len(features) or len(rows) != marker['pairs'] * 2:
        raise RuntimeError('Intervention rows do not match the predeclared paired acquisition')
    pdm, vision = Detector(ARTIFACTS / 'pdm.pt'), VisionDetector(ARTIFACTS / 'vision.onnx')
    grouped = {}
    for row, feature in zip(rows, features):
        image = root / row['image']
        if hashlib.sha256(image.read_bytes()).hexdigest() != row['image_sha256']:
            raise RuntimeError('Intervention image changed')
        health = pdm.infer(feature)
        detections = vision.infer(cv2.imread(str(image)))
        pair = grouped.setdefault(row['pair_id'], {})
        if row['fault_level'] in pair:
            raise RuntimeError('Duplicate intervention arm in a matched family')
        pair[row['fault_level']] = {**row,
            'vibration_rms': float(feature[1]), 'health_index': health['health_index'],
            'detected_defective': bool(detections), 'detections': detections}
    contrasts = {name: [] for name in ('vibration_rms', 'health_index', 'ground_truth_defective', 'detected_defective')}
    for pair in grouped.values():
        low, high = pair[0], pair[5]
        for key in ('camera_pose', 'light', 'background_color', 'product_color', 'x', 'y', 'yaw', 'dx', 'dy', 'defect_yaw'):
            if low['scene'][key] != high['scene'][key]:
                raise RuntimeError('A nuisance variable changed between intervention arms')
        if low['joint_baseline'] != high['joint_baseline'] or low['shared_uniform'] != high['shared_uniform']:
            raise RuntimeError('The sensor/quality intervention pair lacks a shared baseline')
        for name in contrasts:
            contrasts[name].append(float(high[name]) - float(low[name]))
    effects = {name: paired_effect(values) for name, values in contrasts.items()}
    report = {**marker, 'effects': effects, 'results': list(grouped.values()),
        'method': 'Matched, randomized-order do(Fault_Level=5) versus do(Fault_Level=0); paired-family bootstrap',
        'limitations': 'Fault level intentionally controls both vibration and generated defect probability. This intervention validates that structural mechanism in the simulator, not an independently discovered physical mechanism or real-factory causality.',
        'models_frozen': {name: hashlib.sha256((ARTIFACTS / name).read_bytes()).hexdigest() for name in ('pdm.pt', 'vision.pt', 'vision.onnx')}}
    report['passed'] = effects['vibration_rms']['bootstrap95'][0] > 0 and effects['ground_truth_defective']['bootstrap95'][0] > 0 and effects['detected_defective']['bootstrap95'][0] > 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'effects': effects}, indent=2))
    if not report['passed']:
        raise RuntimeError('The controlled intervention did not meet its declared success criterion')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=DATA / 'causal')
    parser.add_argument('--output', type=Path, default=verification_path('root-cause.json'))
    args = parser.parse_args()
    verify(args.data, args.output)
