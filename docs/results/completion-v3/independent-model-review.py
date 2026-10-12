"""Rebuild the frozen RUL network independently and replay every untouched test sequence."""
import json
from pathlib import Path
import numpy as np
import torch
from torch import nn


class IndependentLSTM(nn.Module):
    """Reconstruct the declared recurrent regression architecture without source imports."""
    def __init__(self, feature_count, hidden):
        """Match checkpoint keys while creating new model code for independent execution."""
        super().__init__()
        self.encoder = nn.LSTM(feature_count, hidden, batch_first=True)
        self.head = nn.Sequential(nn.Linear(hidden, 16), nn.ReLU(), nn.Linear(16, 1), nn.Sigmoid())

    def forward(self, sequence):
        """Calculate a scalar only from the final causal recurrent state."""
        encoded, _ = self.encoder(sequence)
        return self.head(encoded[:, -1]).squeeze(-1)


def review():
    """Recreate test windows and normalization from frozen checkpoint parameters only."""
    root = Path('/review')
    saved = torch.load(root / 'artifacts/rul.pt', weights_only=False, map_location='cpu')
    with np.load(root / 'data/optional-rul-v1/rul/dataset.npz') as archive:
        data = {name: archive[name] for name in archive.files}
    reported = json.loads((root / 'docs/results/completion-v3/rul.json').read_text())
    x, truth, identifiers = [], [], []
    units = sorted(set(data['unit_ids'][data['splits'] == 'test']))
    for unit in units:
        indices = np.flatnonzero((data['unit_ids'] == unit) & (data['splits'] == 'test'))
        indices = indices[np.argsort(data['age_seconds'][indices])]
        for end in range(saved['sequence_length'] - 1, len(indices)):
            x.append(data['features'][indices[end-saved['sequence_length']+1:end+1]])
            truth.append(data['rul_seconds'][indices[end]])
            identifiers.append((unit, float(data['age_seconds'][indices[end]])))
    x = np.asarray(x)
    x[..., 1:] = np.log1p(np.maximum(x[..., 1:], 0))
    x = torch.as_tensor((x-saved['mean'])/saved['std'], dtype=torch.float32)
    model = IndependentLSTM(len(saved['feature_names']), saved['hidden_size']).eval()
    model.load_state_dict(saved['model'])
    torch.set_num_threads(4)
    with torch.inference_mode():
        predicted = model(x).numpy() * saved['target_scale']
    recorded = np.asarray([r['predicted_rul_seconds'] for r in reported['predictions']])
    expected_ids = [(r['unit_id'], r['age_seconds']) for r in reported['predictions']]
    mae = float(np.mean(np.abs(predicted-truth)))
    difference = float(np.max(np.abs(predicted-recorded)))
    return {'passed': identifiers == expected_ids and difference < 1e-5 and abs(mae-reported['test']['mae_seconds']) < 1e-5,
            'test_units': len(units), 'test_sequences': len(x), 'independent_test_mae_seconds': mae,
            'maximum_difference_from_reported_predictions_seconds': difference,
            'fit_or_selection_performed': False, 'network': 'LSTM9x32 then Linear32x16/ReLU/Linear16x1/Sigmoid',
            'torch_version': torch.__version__, 'hardware_scope': 'CPU inference in a separate network-disabled container'}


if __name__ == '__main__':
    result = review()
    Path('/independent/independent-model-review.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
