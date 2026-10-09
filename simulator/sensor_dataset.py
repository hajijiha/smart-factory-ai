"""Generate virtual sensor windows from the live Gazebo joint baseline, atomically."""
import json
import logging
import time
import numpy as np
from factory.common import CONFIG, DATA, utc_now
from factory.pdm.features import extract
from simulator.signal import vibration
from simulator.scenarios import generator_version, groups, sensor_records
from simulator.dataset_protocol import specification, isolated_directory, cached, complete, verify_plugin_binding


def generate_sensor(node):
    """Record 5,000 normal and 2,000 fault windows with separate scenario seeds."""
    if generator_version() == 'v2':
        return generate_sensor_v2(node)
    path=DATA/'sensor/dataset.npz'
    if path.exists():
        return
    deadline=time.monotonic()+30
    with node.condition:
        while node.joint is None or abs(node.joint['velocity'][0])<1:
            if time.monotonic()>deadline:
                raise TimeoutError('Real Gazebo motor observation not available')
            node.condition.wait(.1)
    features,levels,splits,provenance=[],[],[],[]
    examples={}
    benchmark=None
    for split,ratio,seed in [('train',.70,401),('val',.15,502),('test',.15,603)]:
        rng=np.random.default_rng(seed)
        normal=int(CONFIG['training']['sensor_normal']*ratio)
        fault=int(CONFIG['training']['sensor_fault']*ratio)
        for index in range(normal+fault):
            level=0 if index<normal else int(rng.integers(2,11))
            with node.condition:
                joint=dict(node.joint)
            wave=vibration(level,joint['velocity'][0],joint['position'][0]+rng.uniform(0,6.28),rng,
                     noise={'train':.012,'val':.013,'test':.014}[split])
            vector,_=extract(wave,rotation_hz=abs(joint['velocity'][0])/(2*np.pi))
            features.append(vector); levels.append(level); splits.append(split)
            provenance.append({'joint':joint,'timestamp':utc_now(),'level':level,'split':split})
            benchmark=wave
            if level==0 and 'normal_wave' not in examples:
                examples['normal_wave']=wave
            if level==10 and 'fault_wave' not in examples:
                examples['fault_wave']=wave
            if index%250==0:
                logging.info('Gazebo sensor generation %s %d/%d',split,index,normal+fault)
                time.sleep(.01)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('dataset.pending.npz')
    np.savez_compressed(temporary,features=features,levels=levels,splits=splits,
                        benchmark_wave=benchmark,
                        benchmark_rotation_hz=abs(joint['velocity'][0])/(2*np.pi),**examples)
    temporary.replace(path)
    (path.parent/'provenance.jsonl').write_text('\n'.join(json.dumps(row) for row in provenance)+'\n')
    logging.info('Sensor dataset completed: %d records',len(levels))


def generate_sensor_v2(node):
    """Group-held-out, balanced severity data, using physically observed RPM."""
    import hashlib
    isolated_directory(DATA, CONFIG)
    root = DATA / 'sensor'
    identity = specification(CONFIG, verify_plugin_binding())
    if cached(root, identity, ('dataset.npz', 'provenance.jsonl')):
        return
    root.mkdir(parents=True, exist_ok=True)
    columns = {name: [] for name in ('features', 'levels', 'splits', 'group_ids', 'fault_types',
        'condition_ids', 'commanded_rpm', 'observed_rpm', 'noise', 'gain', 'load')}
    provenance = []
    examples = {}
    benchmark = None
    for group in groups('sensor', CONFIG):
        p = group['parameters']
        commanded_rpm = p['rotation_hz'] * 60
        # Acquisition.set_motor waits for an acknowledged command and settled joint observation.
        node.set_motor(commanded_rpm)
        rng = np.random.default_rng(group['seed'] + 303)
        for index, (level, kind) in enumerate(sensor_records(group, CONFIG)):
            with node.condition:
                joint = dict(node.joint)
            observed_rpm = abs(joint['velocity'][0]) * 60 / (2 * np.pi)
            if abs(observed_rpm - commanded_rpm) > max(3., commanded_rpm * .01):
                raise RuntimeError('Gazebo motor left the requested operating condition')
            wave = vibration(level, joint['velocity'][0], joint['position'][0] + rng.uniform(0, 2 * np.pi),
                rng, sampling_hz=CONFIG['sensor']['sampling_hz'], samples=CONFIG['sensor']['window_samples'],
                fault_type=kind, parameters=p)
            vector, _ = extract(wave, sampling_hz=CONFIG['sensor']['sampling_hz'], rotation_hz=observed_rpm / 60)
            row = {'joint': joint, 'timestamp': utc_now(), 'level': level, 'split': group['split'],
                'generator_version': 'v2', 'fingerprint': identity['fingerprint'],
                'group_id': group['group_id'], 'condition_id': group['condition_id'],
                'seed': group['seed'], 'sample_index': index, 'fault_type': kind,
                'commanded_rpm': commanded_rpm, 'observed_rpm': observed_rpm,
                'signal_params': p, 'noise': p['noise'], 'gain': p['gain'], 'load': p['load'],
                'wave_sha256': hashlib.sha256(np.asarray(wave, dtype='<f8').tobytes()).hexdigest(),
                'feature_sha256': hashlib.sha256(np.asarray(vector, dtype='<f8').tobytes()).hexdigest()}
            values = {'features': vector, 'levels': level, 'splits': group['split'],
                'group_ids': group['group_id'], 'fault_types': kind, 'condition_ids': group['condition_id'],
                'commanded_rpm': commanded_rpm, 'observed_rpm': observed_rpm,
                'noise': p['noise'], 'gain': p['gain'], 'load': p['load']}
            for name, value in values.items():
                columns[name].append(value)
            provenance.append(row)
            benchmark = wave
            if level == 0 and 'normal_wave' not in examples:
                examples['normal_wave'] = wave
            if level == 10 and 'fault_wave' not in examples:
                examples['fault_wave'] = wave
        logging.info('v2 sensor family %s: %d windows', group['group_id'], len(provenance))
    temporary = root / 'dataset.pending.npz'
    np.savez_compressed(temporary, **columns, generator_version='v2', fingerprint=identity['fingerprint'],
        schema_version=2, benchmark_wave=benchmark, benchmark_rotation_hz=observed_rpm / 60, **examples)
    temporary.replace(root / 'dataset.npz')
    pending = root / 'provenance.pending.jsonl'
    pending.write_text('\n'.join(json.dumps(row, sort_keys=True, allow_nan=False) for row in provenance) + '\n')
    pending.replace(root / 'provenance.jsonl')
    from collections import Counter
    complete(root, identity, ('dataset.npz', 'provenance.jsonl'), len(provenance),
        split_counts=dict(Counter(columns['splits'])),
        level_counts={str(k): v for k, v in Counter(columns['levels']).items()},
        group_counts={split: len(set(g for g, s in zip(columns['group_ids'], columns['splits']) if s == split))
            for split in ('train', 'val', 'test')},
        split_method='Independent operating families, assigned before samples; all labels mixed within each family')
