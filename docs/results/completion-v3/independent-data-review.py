"""Independently verify RUL acquisition and persisted evaluation without project imports."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
import numpy as np

WORKSPACE = Path(__file__).resolve().parents[2]
SOURCE = WORKSPACE / 'smart-factory-ai'
RUNTIME = Path(r'\\wsl.localhost\Ubuntu\home\gkwlgns02\smart-factory-completion-20261012')
ROOT = RUNTIME / 'data/optional-rul-v1/rul'


def digest(path):
    """Hash preserved file bytes without mutating them."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def independently_extract(wave, rotation):
    """Recompute the nine documented statistics with independent NumPy formulas."""
    centered = wave - wave.mean()
    moment2 = np.mean(centered ** 2)
    shape_kurtosis = np.mean(centered ** 4) / moment2 ** 2
    rms, peak = np.sqrt(np.mean(wave ** 2)), np.max(np.abs(wave))
    window = np.hanning(len(wave))
    amplitudes = np.abs(np.fft.rfft(centered * window)) * 2 / window.sum()
    frequencies = np.fft.rfftfreq(len(wave), 1 / 2048)
    ratio = 7.94 / 39
    bpfo, bpfi = 4 * rotation * (1 - ratio), 4 * rotation * (1 + ratio)
    band = lambda f: amplitudes[np.abs(frequencies - f) <= 2].max()
    return np.asarray([wave.mean(), rms, peak, peak / rms, shape_kurtosis,
                       frequencies[1 + np.argmax(amplitudes[1:])], np.sum(amplitudes ** 2), band(bpfo), band(bpfi)])


def review():
    """Audit complete source identities, independent units, causal labels and raw features."""
    identity = json.loads((ROOT / 'COMPLETE').read_text())
    with np.load(ROOT / 'dataset.npz') as archive:
        data = {name: archive[name] for name in archive.files}
    rows = [json.loads(line) for line in (ROOT / 'provenance.jsonl').read_text().splitlines()]
    plans = {p['unit_id']: p for p in json.loads((ROOT / 'units.json').read_text())}
    metric = json.loads((SOURCE / 'docs/results/completion-v3/rul.json').read_text())
    split_units = {s: set(data['unit_ids'][data['splits'] == s]) for s in ('train', 'val', 'test')}
    failures = []
    def require(condition, message):
        """Collect concrete failed invariants instead of discarding later checks."""
        if not condition:
            failures.append(message)
    require(all(digest(ROOT / name) == sha for name, sha in identity['files'].items()), 'dataset file hash mismatch')
    require(not any(split_units[a] & split_units[b] for a, b in [('train', 'val'), ('train', 'test'), ('val', 'test')]), 'unit leakage')
    require(len(rows) == len(data['features']) == identity['row_count'], 'row count mismatch')
    previous = {}
    rngs = {unit: np.random.default_rng(plan['seed'] + 10000) for unit, plan in plans.items()}
    maximum_error = 0.
    for i, row in enumerate(rows):
        unit, age = row['unit_id'], row['age_seconds']
        plan, joint = plans[unit], row['joint']
        require(unit == data['unit_ids'][i] and row['split'] == data['splits'][i] == plan['split'], f'identity mismatch row {i}')
        require(data['rul_seconds'][i] == row['rul_seconds'] == plan['failure_seconds'] - age, f'label mismatch row {i}')
        require(age == data['age_seconds'][i], f'age mismatch row {i}')
        require(datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).utcoffset().total_seconds() == 0, f'non-UTC row {i}')
        if unit in previous:
            require(age - previous[unit][0] == 1 and joint['sim_time'] > previous[unit][1], f'nonchronological unit {unit}')
        previous[unit] = (age, joint['sim_time'])
        require(hashlib.sha256(np.asarray(data['features'][i], dtype='<f8').tobytes()).hexdigest() == row['feature_sha256'], f'feature provenance mismatch row {i}')
        fraction = age / plan['failure_seconds']
        level = 9 * (plan['initial_wear_fraction'] + (1-plan['initial_wear_fraction']) * fraction ** plan['wear_exponent'])
        require(abs(level-row['injected_wear_level']) < 1e-12, f'wear mismatch row {i}')
        omega, phase = joint['velocity'][0], joint['position'][0]
        rotation, severity = abs(omega) / (2*np.pi), level / 10
        t = np.arange(2048) / 2048
        wave = .16 * (rotation/25)**2 * np.sin(omega*t+phase)
        wave += .6*severity*np.sin(omega*t+phase)
        wave += .4*severity**1.4*np.sin(2*omega*t)+.25*severity*np.sin(3*omega*t)
        for frequency in (4*rotation*(1-7.94/39), 4*rotation*(1+7.94/39)):
            pulse_phase = (t*frequency + phase/(2*np.pi)) % 1
            wave += 1.1*severity*np.exp(-pulse_phase*22)*np.cos(2*np.pi*350*t)
        wave += rngs[unit].normal(0, plan['noise']+.16*severity, 2048)
        maximum_error = max(maximum_error, float(np.max(np.abs(independently_extract(wave, rotation)-data['features'][i]))))
    require(maximum_error < 1e-8, 'independent feature replay mismatch')
    require(all(abs(p['endpoint_roller_velocity']) <= .01 for p in plans.values()), 'physical failure endpoints not stopped')
    truth = np.array([p['true_rul_seconds'] for p in metric['predictions']])
    predictions = np.array([p['predicted_rul_seconds'] for p in metric['predictions']])
    require(set(p['unit_id'] for p in metric['predictions']) == split_units['test'], 'evaluation contains non-test units')
    mae, rmse = float(np.mean(np.abs(predictions-truth))), float(np.sqrt(np.mean((predictions-truth)**2)))
    require(abs(mae-metric['test']['mae_seconds']) < 1e-10, 'reported MAE mismatch')
    require(abs(rmse-metric['test']['rmse_seconds']) < 1e-10, 'reported RMSE mismatch')
    require(digest(SOURCE / 'artifacts/rul.pt') == metric['model_sha256'], 'checkpoint hash mismatch')
    return {'passed': not failures, 'failures': failures, 'rows': len(rows), 'unit_counts': {s:len(u) for s,u in split_units.items()},
            'maximum_absolute_feature_replay_error': maximum_error, 'recomputed_test_mae_seconds': mae,
            'recomputed_test_rmse_seconds': rmse, 'checkpoints_bound': True,
            'limits': 'Model forward replay and fresh live behavior require the Docker runtime; JSON prediction metrics alone do not validate network execution.'}


if __name__ == '__main__':
    result = review()
    (Path(__file__).with_name('independent-data-review.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
