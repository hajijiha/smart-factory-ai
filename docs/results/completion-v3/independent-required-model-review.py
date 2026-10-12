"""Recompute mandatory PdM classification metrics from frozen original test data."""
import hashlib
import json
from pathlib import Path
import numpy as np
import torch
from torch import nn


def measurements(truth, predicted):
    """Compute the confusion matrix and metrics without project or sklearn helpers."""
    tp, fp = int(np.sum(truth & predicted)), int(np.sum(~truth & predicted))
    fn, tn = int(np.sum(truth & ~predicted)), int(np.sum(~truth & ~predicted))
    precision, recall = tp/(tp+fp), tp/(tp+fn)
    return {'tp':tp, 'fp':fp, 'fn':fn, 'tn':tn, 'precision':precision, 'recall':recall,
            'f1':2*precision*recall/(precision+recall)}


def review():
    """Restore only frozen train parameters and execute the independent AE on test rows."""
    root = Path('/review')
    saved = torch.load(root/'artifacts/pdm.pt', map_location='cpu', weights_only=False)
    with np.load('/baseline/sensor/dataset.npz') as archive:
        rows = archive['splits'] == 'test'
        features, truth = archive['features'][rows].copy(), archive['levels'][rows] > 0
    features[:, 1:] = np.log1p(np.maximum(features[:, 1:], 0))
    z = (features-saved['mean'])/saved['std']
    chart = np.max(np.abs(z[:, [1,2,6,7,8]]), axis=1)
    network = nn.Sequential(nn.Linear(9,16),nn.ReLU(),nn.Linear(16,3),nn.ReLU(),nn.Linear(3,16),nn.ReLU(),nn.Linear(16,9)).eval()
    network.load_state_dict({name.removeprefix('network.'):value for name,value in saved['model'].items()})
    with torch.inference_mode():
        reconstructed = network(torch.as_tensor(z,dtype=torch.float32)).numpy()
    errors = np.mean((z-reconstructed)**2,axis=1)
    measured = {'chart':measurements(truth,chart > saved['thresholds']['chart']),
                'ae':measurements(truth,errors > saved['thresholds']['ae'])}
    reference = json.loads((root/'docs/results/pdm.json').read_text())
    return {'passed': all(r['f1'] >= .8 for r in measured.values()), 'test_samples':len(z),
            'measurements':measured, 'fitted_on_test':False,
            'checkpoint_sha256':hashlib.sha256((root/'artifacts/pdm.pt').read_bytes()).hexdigest(),
            'dataset_sha256':hashlib.sha256(Path('/baseline/sensor/dataset.npz').read_bytes()).hexdigest()}


if __name__ == '__main__':
    result = review()
    Path('/independent/independent-required-model-review.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
