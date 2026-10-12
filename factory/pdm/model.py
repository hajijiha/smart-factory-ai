"""Normal-only Autoencoder and robust statistical control chart."""
import numpy as np
import torch
from torch import nn
from factory.pdm.features import transform


class Autoencoder(nn.Module):
    """Compress nine standardized vibration features into three latent values."""
    def __init__(self):
        """Build the small CPU inference network."""
        super().__init__()
        self.network = nn.Sequential(nn.Linear(9, 16), nn.ReLU(), nn.Linear(16, 3),
                                     nn.ReLU(), nn.Linear(3, 16), nn.ReLU(), nn.Linear(16, 9))
    def forward(self, x):
        """Reconstruct a feature vector."""
        return self.network(x)


class Detector:
    """Load fitted statistics and convert both errors into calibrated health scores."""
    def __init__(self, path):
        """Load a locally trained checkpoint, including its validation calibration."""
        saved = torch.load(path, map_location='cpu', weights_only=False)
        self.model = Autoencoder().eval()
        self.model.load_state_dict(saved['model'])
        self.mean, self.std = saved['mean'], saved['std']
        self.thresholds, self.calibration = saved['thresholds'], saved['calibration']
        self.dataset_identity = saved.get('dataset_identity')
    def errors(self, features):
        """Return chart maximum deviation and AE reconstruction error."""
        z = (transform(features)-self.mean)/self.std
        with torch.inference_mode():
            reconstructed = self.model(torch.as_tensor(z, dtype=torch.float32)).numpy()
        return np.max(np.abs(z[..., [1, 2, 6, 7, 8]]), axis=-1), np.mean((z-reconstructed)**2, axis=-1)
    def infer(self, features):
        """Calculate bounded scores and a four-stage Health Index without fault labels."""
        chart, ae = self.errors(features)
        scores = [float(np.interp(error, self.calibration[name], [.05, .30, .55, .85, 1.0]))
                  for name, error in [('chart', chart), ('ae', ae)]]
        score = float(np.clip(np.mean(scores), 0, 1))
        health = round(100*(1-score), 2)
        status = 'normal' if health >= 80 else 'caution' if health >= 60 else 'warning' if health >= 40 else 'danger'
        return {'anomaly_score': score, 'health_index': health, 'status': status,
                'chart_score': scores[0], 'ae_score': scores[1],
                'chart_anomaly': bool(chart > self.thresholds['chart']),
                'ae_anomaly': bool(ae > self.thresholds['ae'])}
