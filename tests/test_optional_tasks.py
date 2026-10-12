"""Check drift mathematics, causal RUL windows, frozen references and MQTT history integrity."""
import hashlib
import json
from datetime import datetime, timezone, timedelta
import numpy as np
import pytest
import torch
from factory.pdm.drift import population_stability, histogram, fit_reference, PSIMonitor
from factory.pdm.rul import RULModel, RULPredictor, sequences
from factory.pdm.features import NAMES
from factory.prognostics import Prognostics


def reference(tmp_path):
    """Fit a known training population while poisoning every excluded split and fault row."""
    normal = np.tile(np.linspace(0, 1, 128)[:, None], (1, 9))
    features = np.concatenate([normal, np.full((32, 9), 10000)])
    data = tmp_path / 'reference.npz'
    np.savez(data, features=features, levels=[0] * 144 + [10] * 16, splits=['train'] * 128 + ['test'] * 16 + ['train'] * 16)
    settings = {'features': ['rms'], 'bins': 8, 'smoothing': .5, 'window_samples': 16, 'stride_samples': 8, 'threshold': .25}
    path = tmp_path / 'profile.json'
    return fit_reference(data, settings, path), settings, path


def test_psi_matches_a_known_exact_distribution_contrast():
    """A smoothed 75/25 versus 25/75 contrast has PSI equal to log(3)."""
    assert population_stability([74, 24], [24, 74], 1) == pytest.approx(np.log(3))
    assert population_stability([50, 50], [100, 100]) == 0
    assert population_stability([100, 0], [0, 100]) > 1


@pytest.mark.parametrize('a,b,smoothing', [([0, 0], [1, 1], .5), ([-1, 2], [1, 1], .5), ([1], [1, 2], .5), ([1, 1], [1, 1], 0)])
def test_psi_refuses_empty_negative_or_incompatible_populations(a, b, smoothing):
    """Invalid distributions cannot create a reassuring drift score."""
    with pytest.raises(ValueError):
        population_stability(a, b, smoothing)


def test_fixed_bins_cover_both_unseen_tails():
    """Extreme out-of-domain values remain counted instead of disappearing from PSI."""
    assert histogram([-100, .2, .8, 100], [None, .5, None]).tolist() == [2, 2]
    with pytest.raises(ValueError):
        histogram([np.nan], [None, .5, None])


def test_reference_uses_only_normal_train_and_cannot_be_overwritten(tmp_path):
    """Poisoned test/fault samples cannot influence reference bins or their frozen identity."""
    fitted, settings, path = reference(tmp_path)
    assert fitted['reference_samples'] == 128
    assert max(fitted['features']['rms']['boundaries'][1:-1]) < 1
    with pytest.raises(RuntimeError, match='Preserve'):
        fit_reference(tmp_path / 'reference.npz', settings, path)


def test_population_drift_detects_shift_without_refitting_the_reference(tmp_path):
    """A balanced population is stable, a displaced one drifts, and the profile bytes stay fixed."""
    fitted, settings, path = reference(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monitor = PSIMonitor(fitted, settings)
    for value in np.linspace(0, 1, 16):
        normal = monitor.update(np.full(9, value))
    assert normal['status'] == 'stable' and normal['psi'] < .25
    for _ in range(16):
        shifted = monitor.update(np.full(9, 10.))
    assert shifted['status'] == 'drift' and shifted['psi'] > .25
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_rul_windows_never_cross_units_or_consume_future_features():
    """The first three samples of a unit predict its current RUL, without its fourth sample."""
    data = {'features': np.tile(np.r_[np.arange(4), 10 + np.arange(4)][:, None], (1, 9)),
            'unit_ids': np.array(['a'] * 4 + ['b'] * 4), 'splits': np.array(['train'] * 4 + ['test'] * 4),
            'age_seconds': np.tile(np.arange(4), 2), 'rul_seconds': np.tile([3, 2, 1, 0], 2)}
    x, y, rows = sequences(data, 'train', 3)
    assert x.shape == (2, 3, 9)
    assert x[0, :, 0].tolist() == [0, 1, 2] and y.tolist() == [1, 0]
    assert all(row['unit_id'] == 'a' for row in rows)
    assert x.max() == 3


def test_lstm_regression_is_bounded_and_differentiable():
    """The recurrent network produces two scalar regressions and receives temporal gradients."""
    model = RULModel(hidden_size=4)
    sequence = torch.randn(2, 5, 9, requires_grad=True)
    output = model(sequence)
    assert output.shape == (2,) and torch.all((output >= 0) & (output <= 1))
    output.sum().backward()
    assert sequence.grad is not None and float(sequence.grad.abs().sum()) > 0


def test_rul_runtime_waits_for_history_and_resets_on_a_gap(tmp_path):
    """A long observation gap cannot leave an old complete sequence looking current."""
    model = RULModel(hidden_size=4)
    path = tmp_path / 'rul.pt'
    torch.save({'model': model.state_dict(), 'feature_names': NAMES, 'hidden_size': 4,
        'mean': np.zeros(9), 'std': np.ones(9), 'target_scale': 180, 'sequence_length': 3, 'dataset_fingerprint': 'unit-test'}, path)
    predictor = RULPredictor(path)
    assert predictor.update(np.ones(9), 1)['rul_seconds'] is None
    predictor.update(np.full(9, 2), 2)
    assert predictor.update(np.full(9, 3), 3)['status'] == 'estimated'
    assert predictor.update(np.ones(9), 20)['status'] == 'warming_up'
    predictor.update(np.ones(9), 21)
    assert predictor.update(np.ones(9), 22)['status'] == 'outside_wear_scenario'
    with pytest.raises(ValueError):
        predictor.update(np.full(9, np.nan), 23)


def test_rul_resamples_irregular_arrivals_using_elapsed_seconds(tmp_path):
    """A linear wear history yields the same trained window despite arrival jitter."""
    model = RULModel(hidden_size=4)
    path = tmp_path / 'rul.pt'
    torch.save({'model': model.state_dict(), 'feature_names': NAMES, 'hidden_size': 4,
        'mean': np.zeros(9), 'std': np.ones(9), 'target_scale': 180, 'sequence_length': 3,
        'sample_seconds': 1., 'dataset_fingerprint': 'jitter-test'}, path)
    regular, jittered = RULPredictor(path), RULPredictor(path)
    for stamp in [0., 1., 2., 3.]:
        expected = regular.update(np.full(9, stamp + 1), stamp)
    for stamp in [0., .7, 1.8, 3.]:
        actual = jittered.update(np.full(9, stamp + 1), stamp)
    assert actual['status'] == 'estimated'
    assert actual['rul_seconds'] == expected['rul_seconds']
    assert actual['rms_slope_per_second'] == pytest.approx(1.)
    faster = RULPredictor(path)
    for stamp in [0., .2, .4]:
        short = faster.update(np.full(9, stamp + 1), stamp)
    assert short['status'] == 'warming_up'


class Predictor:
    """Count accepted MQTT windows without needing a trained checkpoint in a unit test."""
    def __init__(self):
        """Start with no accepted chronological observations."""
        self.count = 0

    def update(self, features, timestamp):
        """Record one new feature vector and return a stand-in regression envelope."""
        self.count += 1
        return {'status': 'warming_up', 'rul_seconds': None, 'samples': self.count}


def test_qos_duplicates_and_reversed_events_do_not_pollute_drift_history(tmp_path):
    """QoS retries leave both the recurrent and population histories unchanged."""
    fitted, settings, _ = reference(tmp_path)
    predictor = Predictor()
    worker = Prognostics(predictor, PSIMonitor(fitted, settings))
    stamp = datetime.now(timezone.utc) - timedelta(seconds=1)
    message = {'event_id': 'one', 'timestamp': stamp.isoformat(), 'features': dict.fromkeys(NAMES, .5)}
    assert worker.observe(message)[0]['event_id'] == 'one'
    assert worker.observe(message) == (None, None)
    assert worker.observe({**message, 'event_id': 'old', 'timestamp': (stamp - timedelta(seconds=1)).isoformat()}) == (None, None)
    assert predictor.count == worker.monitor.samples == 1
    with pytest.raises(ValueError, match='future'):
        worker.observe({**message, 'event_id': 'future', 'timestamp': (stamp + timedelta(seconds=20)).isoformat()})
