"""Capture matched equipment interventions with fixed nuisance variables in Gazebo."""
import json
import os
from pathlib import Path
import hashlib
import cv2
import numpy as np
from factory.common import CONFIG, DATA, utc_now
from factory.pdm.features import extract
from simulator.bridge import random_scene
from simulator.rul_dataset import observed_joint
from simulator.signal import vibration
from simulator.dataset_protocol import write_json


def generate_causal(node):
    """Preserve every paired low/high fault outcome without selecting by model predictions."""
    root = Path(os.environ.get('CAUSAL_DATA_DIR', DATA / 'causal'))
    if root.exists() and any(root.iterdir()):
        raise RuntimeError('Preserve prior intervention evidence and choose a new CAUSAL_DATA_DIR')
    root.mkdir(parents=True, exist_ok=True)
    seed, count, levels = 2026101205, 60, (0, 5)
    rng = np.random.default_rng(seed)
    features, rows = [], []
    for pair in range(count):
        joint = observed_joint(node)
        uniform = float(rng.random())
        kind = int(rng.integers(0, 3))
        scene_seed = int(rng.integers(1, 2 ** 31))
        order = tuple(rng.permutation(levels).tolist())
        for level in order:
            probability = .02 + .085 * level
            cls = kind if uniform < probability else -1
            scene = random_scene(np.random.default_rng(scene_seed), cls, 'test', f'intervention-{pair}-{level}')
            image, capture_joint, sim_time = node.capture(scene)
            path = root / f'pair-{pair:03d}-level-{level}.jpg'
            if not cv2.imwrite(str(path), image):
                raise RuntimeError('Could not preserve intervention camera evidence')
            # Both intervention arms share the same observed baseline and random stream.
            wave = vibration(level, joint['velocity'][0], joint['position'][0], np.random.default_rng(scene_seed))
            vector, _ = extract(wave, CONFIG['sensor']['sampling_hz'], abs(joint['velocity'][0]) / (2 * np.pi))
            features.append(vector)
            rows.append({'pair_id': pair, 'fault_level': level, 'ground_truth_defective': cls >= 0,
                'ground_truth_class': cls, 'defect_probability': probability, 'shared_uniform': uniform,
                'scene': scene, 'image': path.name, 'image_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'joint_baseline': joint, 'camera_joint': capture_joint, 'camera_sim_time': sim_time, 'timestamp': utc_now()})
        if pair % 10 == 0:
            print(f'Controlled intervention pairs {pair + 1}/{count}', flush=True)
    np.savez_compressed(root / 'features.npz', features=features)
    write_json(root / 'manifest.json', rows)
    write_json(root / 'COMPLETE', {'schema_version': 1, 'seed': seed, 'pairs': count, 'fault_levels': list(levels),
        'assignment': 'Random arm order, matched camera/light/background/random seed and shared Bernoulli uniform',
        'scope': 'Causal effect of the declared fault intervention inside this simulator; no real-factory causal claim',
        'files': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ('features.npz', 'manifest.json')}})
