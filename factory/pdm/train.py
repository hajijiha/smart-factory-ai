"""Fit two PdM detectors, evaluate scenario holdout, and benchmark five CPU runs."""
import json
import platform
import time
from pathlib import Path
import numpy as np
import torch
from sklearn.metrics import precision_recall_fscore_support
from factory.common import ARTIFACTS, DATA, CONFIG
from factory.pdm.features import extract, transform, NAMES
from factory.pdm.model import Autoencoder, Detector


def choose_threshold(errors, labels):
    """Select the best F1 threshold using validation labels only."""
    candidates = np.unique(np.quantile(errors, np.linspace(.1, .99, 200)))
    values = [precision_recall_fscore_support(labels, errors > threshold, average='binary', zero_division=0)[2]
              for threshold in candidates]
    return float(candidates[np.argmax(values)])


def train():
    """Train on normal windows, calibrate on validation scenarios and evaluate test."""
    torch.set_num_threads(4)
    torch.manual_seed(20261008)
    dataset = np.load(DATA/'sensor/dataset.npz')
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
                  'thresholds': {}, 'calibration': {}, 'feature_names': NAMES}
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, ARTIFACTS/'pdm.pt')
    detector = Detector(ARTIFACTS/'pdm.pt')
    chart, ae = detector.errors(x[val])
    for name, errors in [('chart', chart), ('ae', ae)]:
        checkpoint['thresholds'][name] = choose_threshold(errors, levels[val] > 0)
        refs = [np.median(errors[levels[val] == level]) for level in [0, 3, 6, 9]]
        refs.append(float(np.max(errors)))
        checkpoint['calibration'][name] = np.maximum.accumulate(np.array(refs)+np.arange(5)*1e-6).tolist()
    torch.save(checkpoint, ARTIFACTS/'pdm.pt')
    detector = Detector(ARTIFACTS/'pdm.pt')
    chart, ae = detector.errors(x[test])
    metrics = {}
    for name, errors in [('chart', chart), ('ae', ae)]:
        p, r, f, _ = precision_recall_fscore_support(levels[test] > 0, errors > detector.thresholds[name], average='binary', zero_division=0)
        metrics[name] = {'precision': float(p), 'recall': float(r), 'f1': float(f)}
    wave = dataset['benchmark_wave']
    timings = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(100):
            feature, _ = extract(wave)
            detector.infer(feature)
        timings.append((time.perf_counter()-start)*10)
    report = {'metrics': metrics, 'cpu_ms_5_runs': timings, 'mean_cpu_ms': float(np.mean(timings)),
              'counts': {split: int(np.sum(splits == split)) for split in ['train', 'val', 'test']},
              'normal_only_training_windows': int(normal_train.sum()), 'epochs':180, 'seed':20261008,
              'thresholds': checkpoint['thresholds'], 'calibration': checkpoint['calibration'],
              'final_training_loss': losses[-1], 'python': platform.python_version(), 'torch': torch.__version__,
              'split_method': 'scenario holdout, distinct seeds/RPM/noise settings; 70/15/15 per class'}
    reports = Path(CONFIG['paths']['reports'])
    reports.mkdir(parents=True, exist_ok=True)
    (reports/'pdm.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    train()
