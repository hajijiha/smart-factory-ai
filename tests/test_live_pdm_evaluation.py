"""Live waveform join integrity without publishing controls or loading model weights."""
from datetime import datetime, timedelta, timezone

import pytest
from scripts.evaluate_live_pdm import Recorder, infer_blind


def observation(event='e', run='r', offset=0):
    """Construct timestamped sample metadata relative to the actual test clock."""
    return {'run_id': run, 'event_id': event, 'timestamp': (datetime.now(timezone.utc)+timedelta(seconds=offset)).isoformat(),
            'fault_level': 1}


def test_live_collector_joins_out_of_order_deliveries_without_duplicate_or_old_epoch_samples():
    """Health-first ordering works; duplicate and retired-run packets cannot increase n."""
    recorder = Recorder('r')
    source = observation()
    start = datetime.now(timezone.utc)-timedelta(seconds=1)
    recorder.receive('factory/health', source)
    assert recorder.candidates(1, start, set()) == []
    recorder.receive('factory/sensor', source)
    recorder.receive('factory/sensor', source)
    recorder.receive('factory/sensor', observation(event='other', run='retired'))
    rows = recorder.candidates(1, start, set())
    assert len(rows) == 1
    assert rows[0]['event_id'] == 'e'
    assert recorder.candidates(1, start, {'e'}) == []


def test_live_collector_excludes_old_condition_stale_and_future_observations():
    """Fresh health cannot refresh a stale waveform or a pre-command sample."""
    recorder = Recorder('r')
    start = datetime.now(timezone.utc)
    for index, offset in enumerate([-10, -1, 10]):
        source = observation(event=str(index), offset=offset)
        recorder.receive('factory/sensor', source)
        recorder.receive('factory/health', source)
    assert recorder.candidates(1, start, set()) == []


def test_nonfinite_waveform_cannot_produce_a_normal_result():
    """Invalid sensor input is rejected before reaching either model."""
    class DetectorSpy:
        """Fail if corrupt data is allowed to reach model inference."""
        def infer(self, features):
            """Reject unexpected inference invocation from the test."""
            raise AssertionError('Invalid waveform reached the detector')
    with pytest.raises(ValueError):
        infer_blind(DetectorSpy(), [float('nan')]*2048, 2048, 955)
