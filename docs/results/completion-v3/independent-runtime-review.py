"""Independent cadence and PSI mathematical oracles in an isolated CPU process."""
import json
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, '/review')
from factory.pdm.rul import RULPredictor
from factory.pdm.drift import PSIMonitor
from factory.common import CONFIG
from factory.pdm.features import NAMES


def prediction_at_cadence(times):
    """Send the same continuous trend sampled at different cadences to a frozen predictor."""
    predictor = RULPredictor('/review/artifacts/rul.pt')
    for t in times:
        features = np.arange(1, 10) * .05 + float(t) * .002
        output = predictor.update(features, 1000 + float(t))
    return output, predictor


def review():
    """Use independent signal-time and fixed-histogram calculations as test oracles."""
    at_one, _ = prediction_at_cadence(np.arange(31))
    at_two, predictor = prediction_at_cadence(np.arange(0, 31, 2))
    at_irregular, _ = prediction_at_cadence(np.r_[np.arange(0, 29, 1.3), 30])
    gap = predictor.update(np.ones(9), 1100)
    profile = json.loads(Path('/review/artifacts/drift-reference.json').read_text())
    monitor = PSIMonitor(profile, CONFIG['drift'])
    outputs = []
    sample = np.full(9, 1000.)
    for _ in range(CONFIG['drift']['window_samples']):
        outputs.append(monitor.update(sample))
    scores = {}
    for name, column in profile['features'].items():
        edges = np.asarray([-np.inf, *column['boundaries'][1:-1], np.inf])
        observed = np.histogram(np.full(CONFIG['drift']['window_samples'], sample[NAMES.index(name)]), edges)[0]
        expected = np.asarray(column['counts'], dtype=float)
        smooth = profile['smoothing']
        p, q = (expected+smooth)/(expected.sum()+smooth*len(expected)), (observed+smooth)/(observed.sum()+smooth*len(observed))
        scores[name] = float(np.sum((q-p)*np.log(q/p)))
    errors = {name: abs(score-outputs[-1]['feature_psi'][name]) for name, score in scores.items()}
    passed = at_one['status'] == at_two['status'] == at_irregular['status'] == 'estimated'
    passed &= at_one['rul_seconds'] == at_two['rul_seconds'] == at_irregular['rul_seconds']
    passed &= abs(at_two['rms_slope_per_second']-.002) < 1e-12
    passed &= gap['status'] == 'warming_up' and gap['rul_seconds'] is None
    passed &= all(item is None for item in outputs[:-1]) and outputs[-1]['status'] == 'drift'
    passed &= max(errors.values()) < 1e-12
    return {'passed': bool(passed), 'cadence_one_second': at_one, 'cadence_two_seconds': at_two,
            'cadence_irregular': at_irregular, 'gap_result': gap,
            'independent_psi': scores, 'maximum_psi_formula_disagreement': max(errors.values()),
            'psi_complete_window_only': all(item is None for item in outputs[:-1]),
            'controls_sent': False, 'live_broker_access': False}


if __name__ == '__main__':
    result = review()
    Path('/independent/independent-runtime-review.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
