"""Population Stability Index using immutable normal-training feature histograms."""
from collections import deque
import hashlib
import json
from pathlib import Path
import numpy as np
from factory.pdm.features import NAMES


def histogram(values, boundaries):
    """Count all finite observations including both tails of the fixed reference bins."""
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError('PSI observations must be finite')
    edges = [-np.inf if index == 0 else np.inf if index == len(boundaries) - 1 else boundary
             for index, boundary in enumerate(boundaries)]
    return np.histogram(values, edges)[0]


def population_stability(reference_counts, actual_counts, smoothing=.5):
    """Compare fixed-bin proportions with additive smoothing and natural logarithms."""
    expected, actual = np.asarray(reference_counts, dtype=float), np.asarray(actual_counts, dtype=float)
    if expected.shape != actual.shape or expected.ndim != 1 or not len(expected):
        raise ValueError('PSI count vectors must have equal nonempty shape')
    if not np.isfinite(expected).all() or not np.isfinite(actual).all() or (expected < 0).any() or (actual < 0).any() or expected.sum() <= 0 or actual.sum() <= 0 or smoothing <= 0:
        raise ValueError('PSI requires valid counts and positive smoothing')
    p = (expected + smoothing) / (expected.sum() + smoothing * len(expected))
    q = (actual + smoothing) / (actual.sum() + smoothing * len(actual))
    return float(np.sum((q - p) * np.log(q / p)))


def fit_reference(dataset_path, settings, destination):
    """Fit bin edges and counts on normal training windows only, then freeze the profile."""
    dataset_path, destination = Path(dataset_path), Path(destination)
    if destination.exists():
        raise RuntimeError('Preserve the drift reference and choose a new output profile')
    data = np.load(dataset_path)
    selection = (data['splits'] == 'train') & (data['levels'] == 0)
    training = data['features'][selection]
    if len(training) < settings['bins'] * 10 or not np.isfinite(training).all():
        raise ValueError('Insufficient or nonfinite normal-training reference samples')
    columns = {}
    for name in settings['features']:
        index = NAMES.index(name)
        edges = np.unique(np.quantile(training[:, index], np.arange(1, settings['bins']) / settings['bins']))
        boundaries = [None, *edges.tolist(), None]
        columns[name] = {'boundaries': boundaries, 'counts': histogram(training[:, index], boundaries).tolist()}
    report = {'schema_version': 1, 'features': columns, 'reference_samples': len(training),
              'selection': 'Only normal training windows; no validation, test or live fitting',
              'dataset_sha256': hashlib.sha256(dataset_path.read_bytes()).hexdigest(), 'smoothing': settings['smoothing'],
              'generator_version': str(data['generator_version']) if 'generator_version' in data else 'v1'}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


class PSIMonitor:
    """Evaluate a sliding feature population without updating the training reference."""
    def __init__(self, reference, settings):
        """Validate the reference histogram and initialize bounded online populations."""
        self.reference = reference
        self.settings = dict(settings)
        self.window = deque(maxlen=int(settings['window_samples']))
        self.samples = 0
        if self.window.maxlen < 16 or int(settings['stride_samples']) < 1:
            raise ValueError('PSI needs a positive stride and at least sixteen window samples')
        for name, profile in reference['features'].items():
            if name not in NAMES or len(profile['counts']) != len(profile['boundaries']) - 1:
                raise ValueError('Malformed PSI reference feature or bin counts')
            if profile['boundaries'][0] is not None or profile['boundaries'][-1] is not None or np.any(np.diff(profile['boundaries'][1:-1]) <= 0):
                raise ValueError('PSI reference boundaries must be ordered with open tails')
        self.latest = {'status': 'warming_up', 'psi': None, 'samples': 0, 'required_samples': self.window.maxlen}

    def update(self, features):
        """Return a new PSI evaluation only at complete-window cadence boundaries."""
        values = np.asarray(features, dtype=float)
        if values.shape != (len(NAMES),) or not np.isfinite(values).all():
            raise ValueError('PSI requires the complete finite vibration feature vector')
        self.window.append(values)
        self.samples += 1
        if len(self.window) < self.window.maxlen:
            self.latest = {'status': 'warming_up', 'psi': None, 'samples': len(self.window), 'required_samples': self.window.maxlen}
            return None
        if (self.samples - self.window.maxlen) % self.settings['stride_samples']:
            return None
        data = np.asarray(self.window)
        scores = {name: population_stability(profile['counts'], histogram(data[:, NAMES.index(name)], profile['boundaries']),
                                             self.reference['smoothing']) for name, profile in self.reference['features'].items()}
        maximum = max(scores.values())
        self.latest = {'status': 'drift' if maximum >= self.settings['threshold'] else 'stable', 'psi': maximum,
                       'feature_psi': scores, 'threshold': self.settings['threshold'], 'samples': len(self.window),
                       'required_samples': self.window.maxlen, 'reference_samples': self.reference['reference_samples'],
                       'reference_dataset_sha256': self.reference['dataset_sha256']}
        return dict(self.latest)
