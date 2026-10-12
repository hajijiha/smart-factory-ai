"""LSTM regression of remaining virtual operating seconds from vibration trends."""
from collections import deque
import numpy as np
import torch
from torch import nn
from factory.pdm.features import transform


class RULModel(nn.Module):
    """Summarize a causal vibration-feature sequence with a small CPU LSTM."""
    def __init__(self, features=9, hidden_size=32):
        """Build a recurrent encoder and a bounded remaining-life regression head."""
        super().__init__()
        self.encoder = nn.LSTM(features, hidden_size, batch_first=True)
        self.head = nn.Sequential(nn.Linear(hidden_size, 16), nn.ReLU(), nn.Linear(16, 1), nn.Sigmoid())

    def forward(self, sequence):
        """Predict normalized remaining life using only past and present feature windows."""
        encoded, _ = self.encoder(sequence)
        return self.head(encoded[:, -1]).squeeze(-1)


def sequences(dataset, split, length, stride=1):
    """Build windows within a single split and unit, never across machines or into the future."""
    features, targets = [], []
    identities = []
    for unit in sorted(set(dataset['unit_ids'][dataset['splits'] == split])):
        selection = np.flatnonzero((dataset['unit_ids'] == unit) & (dataset['splits'] == split))
        order = np.argsort(dataset['age_seconds'][selection])
        selection = selection[order]
        for end in range(length - 1, len(selection), stride):
            window = selection[end - length + 1:end + 1]
            features.append(dataset['features'][window])
            targets.append(dataset['rul_seconds'][selection[end]])
            identities.append({'unit_id': str(unit), 'age_seconds': float(dataset['age_seconds'][selection[end]])})
    if not features:
        raise ValueError('No complete RUL sequences in the selected unit split')
    return np.asarray(features), np.asarray(targets), identities


class RULPredictor:
    """Load frozen normalizers and maintain a chronological online feature history."""
    def __init__(self, path, minimum_slope=.00025):
        """Restore fitted parameters without learning from live or test observations."""
        saved = torch.load(path, map_location='cpu', weights_only=False)
        self.model = RULModel(len(saved['feature_names']), saved['hidden_size']).eval()
        self.model.load_state_dict(saved['model'])
        self.mean, self.std = saved['mean'], saved['std']
        self.scale, self.length = saved['target_scale'], saved['sequence_length']
        self.sample_seconds = saved.get('sample_seconds', 1.)
        self.history = deque(maxlen=self.length * 10 + 2)
        self.last_timestamp = None
        self.dataset_fingerprint = saved['dataset_fingerprint']
        self.minimum_slope = minimum_slope

    def update(self, features, timestamp):
        """Resample available past observations to the trained operating-second grid."""
        if self.last_timestamp is not None and not .1 <= timestamp - self.last_timestamp <= 5:
            self.history.clear()
        self.last_timestamp = timestamp
        vector = np.asarray(features, dtype=float)
        if vector.shape != (len(self.mean),) or not np.isfinite(vector).all():
            self.history.clear()
            raise ValueError('RUL features must be finite')
        self.history.append((timestamp, vector))
        grid = timestamp - np.arange(self.length - 1, -1, -1) * self.sample_seconds
        if self.history[0][0] > grid[0]:
            return {'status': 'warming_up', 'rul_seconds': None, 'samples': len(self.history),
                    'required_samples': self.length, 'required_history_seconds': (self.length - 1) * self.sample_seconds}
        times = np.asarray([item[0] for item in self.history])
        observations = np.asarray([item[1] for item in self.history])
        sampled = np.column_stack([np.interp(grid, times, observations[:, i]) for i in range(observations.shape[1])])
        slope = float(np.polyfit(grid - grid[0], sampled[:, 1], 1)[0])
        if slope < self.minimum_slope:
            return {'status': 'outside_wear_scenario', 'rul_seconds': None, 'samples': len(self.history),
                    'required_samples': self.length, 'rms_slope_per_second': slope,
                    'reason': 'A sustained increasing wear trend is required; stationary or recovering states do not have a validated finite RUL.'}
        x = (transform(sampled) - self.mean) / self.std
        with torch.inference_mode():
            estimate = float(self.model(torch.as_tensor(x[None], dtype=torch.float32))[0]) * self.scale
        return {'status': 'estimated', 'rul_seconds': round(estimate, 2), 'samples': len(self.history),
                'scope': 'synthetic_monotonic_wear_proxy', 'dataset_fingerprint': self.dataset_fingerprint,
                'rms_slope_per_second': slope, 'sample_seconds': self.sample_seconds,
                'time_unit': 'virtual operating second of the calibrated synthetic wear scenario'}
