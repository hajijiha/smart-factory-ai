"""Train and select an LSTM on complete Gazebo wear units; evaluate held-out units once."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import numpy as np
import torch
from factory.common import CONFIG, DATA, ARTIFACTS, REPORTS
from factory.pdm.features import transform, NAMES
from factory.pdm.rul import RULModel, sequences


def metrics(truth, predicted):
    """Measure remaining-life error in the dataset's explicit operating-second unit."""
    error = np.asarray(predicted) - np.asarray(truth)
    return {'mae_seconds': float(np.mean(np.abs(error))), 'rmse_seconds': float(np.sqrt(np.mean(error ** 2))),
            'bias_seconds': float(np.mean(error)), 'samples': len(error)}


def train(root, artifacts, reports):
    """Fit train-only normalization, select by validation MAE, then freeze and evaluate test."""
    settings = CONFIG['rul']
    torch.set_num_threads(4)
    torch.manual_seed(settings['seed'])
    identity = json.loads((root / 'COMPLETE').read_text())
    for name, expected in identity['files'].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('RUL acquisition evidence changed after completion')
    dataset = np.load(root / 'dataset.npz')
    unit_sets = {split: set(dataset['unit_ids'][dataset['splits'] == split]) for split in ('train', 'val', 'test')}
    if any(unit_sets[a] & unit_sets[b] for a, b in (('train', 'val'), ('train', 'test'), ('val', 'test'))):
        raise RuntimeError('RUL unit leakage across splits')
    normalizing = transform(dataset['features'][dataset['splits'] == 'train'])
    mean, std = normalizing.mean(axis=0), np.maximum(normalizing.std(axis=0), .005)
    prepared = {}
    for split in ('train', 'val'):
        raw, y, _ = sequences(dataset, split, settings['sequence_length'], stride=2)
        x = torch.as_tensor((transform(raw) - mean) / std, dtype=torch.float32)
        prepared[split] = (x, torch.as_tensor(y / settings['maximum_rul_seconds'], dtype=torch.float32), y)
    model = RULModel(len(NAMES), settings['hidden_size'])
    optimizer = torch.optim.Adam(model.parameters(), lr=.003)
    best, best_error, best_epoch, stale = None, float('inf'), 0, 0
    history = []
    x_train, y_train, _ = prepared['train']
    for epoch in range(settings['epochs']):
        model.train()
        loss_sum = 0.
        for indices in torch.randperm(len(x_train)).split(settings['batch']):
            loss = nn_loss(model(x_train[indices]), y_train[indices])
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            loss_sum += float(loss.detach()) * len(indices)
        model.eval()
        with torch.inference_mode():
            val_predictions = model(prepared['val'][0]).numpy() * settings['maximum_rul_seconds']
        val_error = metrics(prepared['val'][2], val_predictions)['mae_seconds']
        history.append({'epoch': epoch + 1, 'train_mse': loss_sum / len(x_train), 'validation_mae_seconds': val_error})
        if val_error < best_error:
            best, best_error, best_epoch, stale = copy.deepcopy(model.state_dict()), val_error, epoch + 1, 0
        else:
            stale += 1
        if epoch % 10 == 0:
            print(f'RUL epoch {epoch + 1}: validation MAE {val_error:.3f}s', flush=True)
        if stale >= settings['patience']:
            break
    model.load_state_dict(best)
    model.eval()
    saved = {'model': best, 'mean': mean, 'std': std, 'feature_names': NAMES,
             'sequence_length': settings['sequence_length'], 'hidden_size': settings['hidden_size'],
             'target_scale': settings['maximum_rul_seconds'], 'sample_seconds': settings['sample_seconds'],
             'dataset_fingerprint': identity['fingerprint']}
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / 'rul.pt'
    if path.exists():
        raise RuntimeError('Preserve existing RUL weights and select a new artifact directory')
    torch.save(saved, path)
    test_raw, truth, rows = sequences(dataset, 'test', settings['sequence_length'])
    x_test = torch.as_tensor((transform(test_raw) - mean) / std, dtype=torch.float32)
    with torch.inference_mode():
        predictions = model(x_test).numpy() * settings['maximum_rul_seconds']
    baseline = float(np.median(prepared['train'][2]))
    report = {'dataset_fingerprint': identity['fingerprint'], 'unit_counts': {s: len(u) for s, u in unit_sets.items()},
        'test': metrics(truth, predictions), 'constant_train_median_baseline': metrics(truth, np.full_like(truth, baseline)),
        'selected_epoch': best_epoch, 'validation_mae_seconds': best_error, 'history': history,
        'selection': 'Minimum validation-unit MAE; normalization and constant baseline fitted on training units only',
        'test_used_for_fit_or_selection': False, 'feature_inputs': NAMES,
        'excluded_inputs': ['age_seconds', 'failure_seconds', 'wear_level', 'rul_seconds', 'unit_id', 'split'],
        'model_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'scope': identity['failure_definition'], 'time_policy': identity['time_policy'],
        'predictions': [{**row, 'true_rul_seconds': float(y), 'predicted_rul_seconds': float(p)} for row, y, p in zip(rows, truth, predictions)]}
    reports.mkdir(parents=True, exist_ok=True)
    (reports / 'rul.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('history', 'predictions')}, indent=2), flush=True)


def nn_loss(predicted, expected):
    """Minimize mean squared normalized remaining-life error."""
    return torch.mean((predicted - expected) ** 2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=DATA / 'rul')
    parser.add_argument('--artifacts', type=Path, default=ARTIFACTS)
    parser.add_argument('--reports', type=Path, default=REPORTS / 'completion-v3')
    args = parser.parse_args()
    train(args.data, args.artifacts, args.reports)
