"""Acquire independent accelerated run-to-failure units from a physical Gazebo motor."""
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path
import numpy as np
from factory.common import CONFIG, DATA, utc_now
from factory.pdm.features import extract, NAMES
from simulator.signal import vibration
from simulator.dataset_protocol import digest, write_json


def unit_plan(settings):
    """Assign complete wear trajectories to splits before acquiring their samples."""
    count = int(settings['units'])
    if count < 20 or count % 20:
        raise ValueError('RUL units must be a positive multiple of twenty for 70/15/15 splits')
    counts = (count * 70 // 100, count * 15 // 100, count * 15 // 100)
    number = 0
    for split, size, offset in zip(('train', 'val', 'test'), counts, (101, 202, 303)):
        for _ in range(size):
            seed = int(settings['seed']) + offset + number * 7919
            rng = np.random.default_rng(seed)
            lifetime = int(rng.integers(*settings['lifetime_seconds']))
            yield {'unit_id': f'rul-{number:04d}', 'split': split, 'seed': seed,
                   'failure_seconds': lifetime, 'wear_exponent': float(rng.uniform(.90, 1.25)),
                   'initial_wear_fraction': float(rng.uniform(0, .025)),
                   'rpm': 954.92965855137 if number % 4 == 0 else float(rng.uniform(850, 950)),
                   'noise': float(rng.uniform(.009, .015 if split == 'train' else .017))}
            number += 1


def wear_level(age, plan, failure_level):
    """Increase synthetic wear monotonically to the predeclared functional-failure level."""
    fraction = np.clip(age / plan['failure_seconds'], 0, 1)
    progress = plan['initial_wear_fraction'] + (1 - plan['initial_wear_fraction']) * fraction ** plan['wear_exponent']
    return float(failure_level * progress)


def observed_joint(node, after=-1):
    """Wait for an actual newer Gazebo joint observation rather than inventing RPM."""
    deadline = time.monotonic() + 30
    with node.condition:
        while not node.joint or node.joint['sim_time'] <= after or abs(node.joint['velocity'][0]) < 1:
            if time.monotonic() > deadline:
                raise TimeoutError('No newer physical motor observation for RUL acquisition')
            node.condition.wait(.01)
        return dict(node.joint)


def generate_rul(node):
    """Record physical baselines and wear labels in a new, non-overwriting dataset."""
    settings = CONFIG['rul']
    root = Path(os.environ.get('RUL_DATA_DIR', DATA / 'rul'))
    if root.exists() and any(root.iterdir()):
        raise RuntimeError('Preserve the existing RUL dataset and select a new RUL_DATA_DIR')
    root.mkdir(parents=True, exist_ok=True)
    source_names = ('simulator/rul_dataset.py', 'simulator/signal.py', 'factory/pdm/features.py')
    source_root = Path(__file__).resolve().parents[1]
    identity = {'schema_version': 1, 'settings': dict(settings), 'feature_names': NAMES,
                'source_hashes': {name: digest(source_root / name) for name in source_names},
                'source': 'Gazebo ODE joint observation plus declared mathematical wear injection',
                'time_policy': 'Accelerated virtual operating seconds; each one-second signal window is synthesized from a newer physical joint observation. Virtual age is distinct from Gazebo sim_time and UTC.',
                'failure_definition': f"Injected wear level reaches {settings['failure_level']}; conveyor is physically stopped. This is a functional-failure proxy, not a damaged-bearing mechanics model."}
    identity['fingerprint'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    features, remaining, units, splits, ages = [], [], [], [], []
    plans = list(unit_plan(settings))
    provenance_path = root / 'provenance.jsonl'
    with provenance_path.open('w', encoding='utf-8') as stream:
        for plan in plans:
            node.conveyor(True)
            node.set_motor(plan['rpm'])
            rng = np.random.default_rng(plan['seed'] + 10000)
            last_sim_time = -1
            for age in np.arange(0, plan['failure_seconds'] + .001, settings['sample_seconds']):
                joint = observed_joint(node, last_sim_time)
                last_sim_time = joint['sim_time']
                level = wear_level(age, plan, settings['failure_level'])
                wave = vibration(level, joint['velocity'][0], joint['position'][0], rng, noise=plan['noise'],
                                 sampling_hz=CONFIG['sensor']['sampling_hz'], samples=CONFIG['sensor']['window_samples'])
                vector, _ = extract(wave, CONFIG['sensor']['sampling_hz'], abs(joint['velocity'][0]) / (2 * np.pi))
                target = float(plan['failure_seconds'] - age)
                features.append(vector); remaining.append(target); units.append(plan['unit_id']); splits.append(plan['split']); ages.append(age)
                stream.write(json.dumps({'unit_id': plan['unit_id'], 'split': plan['split'], 'age_seconds': float(age),
                    'rul_seconds': target, 'injected_wear_level': level, 'joint': joint, 'timestamp': utc_now(),
                    'feature_sha256': hashlib.sha256(np.asarray(vector, dtype='<f8').tobytes()).hexdigest()}) + '\n')
            node.conveyor(False)
            deadline = time.monotonic() + 10
            while abs(observed_joint(node)['velocity'][1]) > .01:
                if time.monotonic() > deadline:
                    raise TimeoutError('RUL endpoint failed to stop the physical conveyor')
                time.sleep(.02)
            plan['endpoint_roller_velocity'] = observed_joint(node)['velocity'][1]
            stream.flush()
            print(f"RUL {plan['unit_id']} {plan['split']}: {plan['failure_seconds']} simulated operating seconds", flush=True)
    pending = root / 'dataset.pending.npz'
    np.savez_compressed(pending, features=features, rul_seconds=remaining, unit_ids=units, splits=splits, age_seconds=ages)
    pending.replace(root / 'dataset.npz')
    write_json(root / 'units.json', plans)
    identity.update(row_count=len(features), unit_counts=dict(Counter(p['split'] for p in plans)),
                    files={name: digest(root / name) for name in ('dataset.npz', 'provenance.jsonl', 'units.json')})
    write_json(root / 'COMPLETE', identity)
    print(f'RUL acquisition complete: {len(plans)} independent units, {len(features)} windows', flush=True)
