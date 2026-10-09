"""Fit two PdM detectors, evaluate scenario holdout, and benchmark five CPU runs."""
import argparse
import json
import platform
import time
from pathlib import Path
import numpy as np
import torch
from sklearn.metrics import precision_recall_fscore_support
from factory.common import ARTIFACTS, DATA, CONFIG, REPORTS
from factory.provenance import dataset_identity, verify_evaluation_identity, write_training_record
from factory.pdm.features import extract, transform, NAMES
from factory.pdm.model import Autoencoder, Detector


def choose_threshold(errors, labels, normal_fpr_max=None):
    """Select validation F1, optionally subject to a healthy false-alarm budget."""
    candidates = np.unique(np.quantile(errors, np.linspace(.1, .99, 200)))
    if normal_fpr_max is not None:
        labels = np.asarray(labels, dtype=bool)
        if not np.any(~labels):
            raise ValueError('False-alarm calibration requires healthy validation samples')
        candidates = np.unique(np.append(candidates, np.max(errors)))
        candidates = [threshold for threshold in candidates
                      if np.mean(errors[~labels] > threshold) <= normal_fpr_max]
    values = [precision_recall_fscore_support(labels, errors > threshold, average='binary', zero_division=0)[2]
              for threshold in candidates]
    return float(candidates[np.argmax(values)])


def benchmark_rotation(dataset):
    """Recover the actual Gazebo RPM for the recorded benchmark window."""
    if 'benchmark_rotation_hz' in dataset:
        return float(dataset['benchmark_rotation_hz'])
    # Older recordings retain the corresponding joint observation in provenance.
    last = (DATA/'sensor/provenance.jsonl').read_text().splitlines()[-1]
    observation = json.loads(last)['joint']
    return abs(observation['velocity'][0])/(2*np.pi)


def benchmark(detector, dataset):
    """Warm up and time FFT, features, both models and HI over five 100-window runs."""
    wave = dataset['benchmark_wave']
    rotation = benchmark_rotation(dataset)
    for _ in range(10):
        feature, _ = extract(wave, rotation_hz=rotation)
        detector.infer(feature)
    timings = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(100):
            feature, _ = extract(wave, rotation_hz=rotation)
            detector.infer(feature)
        timings.append((time.perf_counter()-start)*10)
    return {'cpu_ms_5_runs':timings, 'mean_cpu_ms':float(np.mean(timings)),
            'benchmark_rotation_hz':rotation, 'benchmark_runs':5,
            'benchmark_windows_per_run':100, 'warmup_windows':10,
            'includes':'Hann FFT, nine features, chart + AE, anomaly score and HI',
            'cpu_threads':4}


def evaluate():
    """Re-evaluate the saved model without fitting or changing any thresholds."""
    torch.set_num_threads(4)
    dataset = np.load(DATA/'sensor/dataset.npz')
    detector = Detector(ARTIFACTS/'pdm.pt')
    identity = dataset_identity(DATA, 'sensor')
    identity_status = verify_evaluation_identity(detector.dataset_identity, identity)
    test = dataset['splits'] == 'test'
    chart, ae = detector.errors(dataset['features'][test])
    metrics = {}
    for name, errors in [('chart',chart),('ae',ae)]:
        p,r,f,_ = precision_recall_fscore_support(dataset['levels'][test]>0,
            errors>detector.thresholds[name],average='binary',zero_division=0)
        metrics[name] = {'precision':float(p),'recall':float(r),'f1':float(f)}
    output = REPORTS/'pdm.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    report = json.loads(output.read_text()) if output.exists() else {}
    report.update(metrics=metrics,python=platform.python_version(),torch=torch.__version__,
        dataset_identity=identity, identity_status=identity_status,
        split_method='group holdout' if identity['generator_version'] == 'v2' else 'scenario holdout, distinct seeds/noise settings, fixed observed Gazebo RPM; 70/15/15 per class',
        **benchmark(detector,dataset))
    output.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)


def train(train_only=False):
    """Train on normal windows, calibrate on validation scenarios and evaluate test."""
    torch.set_num_threads(4)
    torch.manual_seed(20261008)
    dataset = np.load(DATA/'sensor/dataset.npz')
    identity = dataset_identity(DATA, 'sensor')
    x, levels, splits = dataset['features'], dataset['levels'], dataset['splits']
    normal_train = (splits == 'train') & (levels == 0)
    val, test = splits == 'val', splits == 'test'
    xt = transform(x)
    mean = xt[normal_train].mean(axis=0)
    std = np.maximum(xt[normal_train].std(axis=0), .005)
    z = torch.as_tensor((xt[normal_train]-mean)/std, dtype=torch.float32)
    model = Autoencoder()
    optimizer = torch.optim.Adam(model.parameters(), lr=.003)
    losses = []
    for epoch in range(180):
        for indices in torch.randperm(len(z)).split(128):
            batch = z[indices]
            loss = torch.mean((model(batch)-batch)**2)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        losses.append(float(loss.detach()))
    checkpoint = {'model': model.state_dict(), 'mean': mean, 'std': std,
                  'thresholds': {}, 'calibration': {}, 'feature_names': NAMES,
                  'dataset_identity': identity}
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, ARTIFACTS/'pdm.pt')
    detector = Detector(ARTIFACTS/'pdm.pt')
    chart, ae = detector.errors(x[val])
    for name, errors in [('chart', chart), ('ae', ae)]:
        checkpoint['thresholds'][name] = choose_threshold(errors, levels[val] > 0,
            normal_fpr_max=.05 if identity['generator_version'] == 'v2' else None)
        refs = [np.median(errors[levels[val] == level]) for level in [0, 3, 6, 9]]
        refs.append(float(np.max(errors)))
        checkpoint['calibration'][name] = np.maximum.accumulate(np.array(refs)+np.arange(5)*1e-6).tolist()
    torch.save(checkpoint, ARTIFACTS/'pdm.pt')
    write_training_record(ARTIFACTS, 'pdm', identity)
    if train_only:
        REPORTS.mkdir(parents=True, exist_ok=True)
        (REPORTS/'pdm-training.json').write_text(json.dumps({
            'dataset_identity': identity, 'normal_only_training_windows': int(normal_train.sum()),
            'epochs': 180, 'seed': 20261008, 'thresholds': checkpoint['thresholds'],
            'calibration': checkpoint['calibration'], 'test_evaluated': False,
            'test_used_for_fit_or_selection': False,
            'checkpoint_selection': 'fixed architecture/epochs; validation thresholds only',
            'threshold_selection': 'maximize validation F1 subject to normal FPR <= 0.05' if identity['generator_version'] == 'v2' else 'maximize validation F1'
        }, indent=2) + '\n')
        print('Training complete; test and challenge data remain unevaluated.', flush=True)
        return
    detector = Detector(ARTIFACTS/'pdm.pt')
    chart, ae = detector.errors(x[test])
    metrics = {}
    for name, errors in [('chart', chart), ('ae', ae)]:
        p, r, f, _ = precision_recall_fscore_support(levels[test] > 0, errors > detector.thresholds[name], average='binary', zero_division=0)
        metrics[name] = {'precision': float(p), 'recall': float(r), 'f1': float(f)}
    report = {'metrics': metrics, **benchmark(detector,dataset),
              'counts': {split: int(np.sum(splits == split)) for split in ['train', 'val', 'test']},
              'normal_only_training_windows': int(normal_train.sum()), 'epochs':180, 'seed':20261008,
              'thresholds': checkpoint['thresholds'], 'calibration': checkpoint['calibration'],
              'final_training_loss': losses[-1], 'python': platform.python_version(), 'torch': torch.__version__,
              'dataset_identity': identity,
              'split_method': 'group holdout' if identity['generator_version'] == 'v2' else 'scenario holdout, distinct seeds/noise settings, fixed observed Gazebo RPM; 70/15/15 per class'}
    reports = REPORTS
    reports.mkdir(parents=True, exist_ok=True)
    (reports/'pdm.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evaluate-only',action='store_true')
    parser.add_argument('--train-only',action='store_true',help='Fit/select using train/validation only; do not access test metrics')
    args = parser.parse_args()
    if args.evaluate_only and args.train_only:
        parser.error('--evaluate-only and --train-only are mutually exclusive')
    evaluate() if args.evaluate_only else train(args.train_only)
