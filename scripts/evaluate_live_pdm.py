"""Blind frozen-model PdM evaluation on newly received live motor waveforms.

Fault commands define scoring labels only. Inference receives measured waveform,
sampling rate and RPM, never the requested fault level. This operator experiment
stops the conveyor, restores level zero and uses the existing guarded reset API.
"""
import argparse
import gzip
import json
import math
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from factory.common import connect, utc_now
from factory.settings import ARTIFACTS, REPORTS
from factory.provenance import sha256_file


LEVELS = (0, 1, 3, 5)
SETTLE_WINDOWS = 2


def api(base, route, body=None):
    """Use the existing validated operator API rather than bypassing the safety controller."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base.rstrip('/')+route, data=data,
                                     headers={'Content-Type': 'application/json'}, method='POST' if body is not None else 'GET')
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def observed_time(value):
    """Accept only aware UTC timestamps for fresh sample selection."""
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


class Recorder:
    """Bound incoming sensor/health envelopes and join them by the actual sensor event."""
    def __init__(self, run_id):
        """Fix the simulator epoch before the first operator command."""
        self.run_id = run_id
        self.lock = threading.Lock()
        self.rows = OrderedDict()

    def receive(self, topic, message):
        """Record actual MQTT deliveries; old epochs and duplicate deliveries cannot add samples."""
        if message.get('run_id') != self.run_id or not isinstance(message.get('event_id'), str):
            return
        with self.lock:
            event = message['event_id']
            entry = self.rows.setdefault(event, {'event_id': event})
            name = 'sensor' if topic == 'factory/sensor' else 'health'
            entry.setdefault(name, message)
            entry.setdefault(name+'_received_at', utc_now())
            entry.setdefault(name+'_received_monotonic', time.monotonic())
            while len(self.rows) > 256:
                self.rows.popitem(last=False)

    def candidates(self, level, started, used):
        """Select the first fresh joined observations after the applied control command."""
        now = datetime.now(timezone.utc)
        with self.lock:
            result = []
            for entry in self.rows.values():
                sensor, health = entry.get('sensor'), entry.get('health')
                if entry['event_id'] in used or not sensor or not health:
                    continue
                stamp = observed_time(sensor.get('timestamp'))
                if stamp is None or stamp < started or not 0 <= (now-stamp).total_seconds() <= 5:
                    continue
                if sensor.get('fault_level') != level or health.get('timestamp') != sensor['timestamp']:
                    continue
                result.append(dict(entry))
            return result


def infer_blind(detector, waveform, sampling_hz, rpm):
    """Receive only measured inputs, explicitly excluding injected fault labels."""
    import numpy as np
    from factory.pdm.features import extract
    wave = np.asarray(waveform, dtype=float)
    if wave.ndim != 1 or len(wave) != 2048 or not np.isfinite(wave).all() or not math.isfinite(rpm):
        raise ValueError('Invalid measured motor waveform')
    feature, _ = extract(wave, sampling_hz, rpm/60)
    return detector.infer(feature)


def guarded_restore(base):
    """Restore level zero and request manual restart only after fresh safe observations."""
    api(base, '/api/fault', {'fault_level': 0})
    deadline = time.monotonic()+25
    last = None
    while time.monotonic() < deadline:
        last = api(base, '/api/snapshot')
        if last.get('process', {}).get('reset_ready'):
            accepted = api(base, '/api/reset', {})
            return {'level_zero_requested': True, 'safe_reset_accepted': accepted.get('accepted', False)}
        # A running healthy line already satisfies recovery without another start.
        if last.get('process', {}).get('state') == 'running' and (last.get('line') or {}).get('fault_level') == 0:
            return {'level_zero_requested': True, 'already_running_healthy': True}
        time.sleep(.5)
    return {'level_zero_requested': True, 'safe_reset_accepted': False,
            'reason': (last or {}).get('process', {}).get('reset_reason', 'Safe observations not yet available')}


def collect(args):
    """Run the predeclared four levels, preserving source waveforms and deployed health."""
    import numpy as np
    import torch
    from factory.pdm.model import Detector
    from scripts.bias_metrics import binary_metrics
    model_path = ARTIFACTS/'pdm.pt'
    model_hash = sha256_file(model_path)
    if model_hash != args.expected_model_sha256:
        raise ValueError('Live PdM checkpoint differs from the pre-recorded frozen baseline')
    world = api(args.dashboard_url, '/api/world')
    if world.get('stale') or not world.get('state'):
        raise RuntimeError('Fresh physical world observations are required before diagnostic commands')
    run_id = world['state']['run_id']
    recorder = Recorder(run_id)
    client = connect('live-pdm-evaluation-'+uuid.uuid4().hex[:8], ['factory/sensor', 'factory/health'], recorder.receive)
    ready = time.monotonic()+15
    while not client.is_connected():
        if time.monotonic() > ready:
            raise RuntimeError('Live waveform subscription unavailable; no diagnostic command issued')
        time.sleep(.1)
    torch.set_num_threads(4)
    detector = Detector(model_path)
    captured, used, restore = [], set(), None
    try:
        # Keep product flow safe and make the final guarded manual reset explicit.
        api(args.dashboard_url, '/api/stop', {})
        for level in LEVELS:
            started = datetime.now(timezone.utc)
            api(args.dashboard_url, '/api/fault', {'fault_level': level})
            print(f'Collecting level {level}: discard first {SETTLE_WINDOWS}, then first {args.samples} fresh joined windows', flush=True)
            deadline = time.monotonic()+90
            skipped, count = 0, 0
            while count < args.samples:
                if time.monotonic() > deadline:
                    raise RuntimeError(f'Only {count}/{args.samples} valid sensor/health observations at level {level}')
                current = api(args.dashboard_url, '/api/world')
                if not current.get('state') or current['state']['run_id'] != run_id:
                    raise RuntimeError('Simulator epoch changed during the fixed diagnostic experiment')
                for entry in recorder.candidates(level, started, used):
                    used.add(entry['event_id'])
                    if skipped < SETTLE_WINDOWS:
                        skipped += 1
                        continue
                    sensor = entry['sensor']
                    rpm = sensor['rpm']
                    start = time.perf_counter()
                    prediction = infer_blind(detector, sensor['waveform'], sensor['sampling_hz'], rpm)
                    elapsed = (time.perf_counter()-start)*1000
                    captured.append({**entry, 'scoring_fault_level': level, 'blind_prediction': prediction,
                                     'blind_inference_ms': elapsed})
                    count += 1
                    if count == args.samples:
                        break
                time.sleep(.2)
            print(f'Completed level {level}: {count} windows', flush=True)
    finally:
        try:
            restore = guarded_restore(args.dashboard_url)
        finally:
            client.loop_stop()
            client.disconnect()
    if sha256_file(model_path) != model_hash:
        raise RuntimeError('Frozen PdM checkpoint changed during collection')
    output = REPORTS/'factory-web-v1'
    output.mkdir(parents=True, exist_ok=True)
    with gzip.open(output/'live-pdm-observations.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for entry in captured:
            stream.write(json.dumps(entry, allow_nan=False)+'\n')
    metrics = {}
    truth = [entry['scoring_fault_level'] > 0 for entry in captured]
    for name in ('chart', 'ae'):
        predicted = [entry['blind_prediction'][name+'_anomaly'] for entry in captured]
        by_level = {}
        for level in LEVELS:
            indices = [index for index, entry in enumerate(captured) if entry['scoring_fault_level'] == level]
            by_level[str(level)] = binary_metrics([truth[index] for index in indices], [predicted[index] for index in indices])
        agreement = sum(entry['blind_prediction'][name+'_anomaly'] == entry['health'][name+'_anomaly'] for entry in captured)
        metrics[name] = {'aggregate': binary_metrics(truth, predicted), 'per_level': by_level,
                         'deployed_flag_agreement': agreement, 'deployed_observations': len(captured)}
    timings = [entry['blind_inference_ms'] for entry in captured]
    rpms = [entry['sensor']['rpm'] for entry in captured]
    report = {'timestamp': utc_now(), 'run_id': run_id, 'model_sha256': model_hash,
              'model_status': 'Frozen baseline; no fitting or threshold change', 'waveforms': len(captured),
              'levels': list(LEVELS), 'samples_per_level': args.samples, 'settle_windows_per_level': SETTLE_WINDOWS,
              'sampling_policy': 'First fresh joined sensor/health events after each applied level, excluding only two predeclared settling windows',
              'blind_inputs': ['waveform', 'sampling_hz', 'measured_motor_rpm'], 'metrics': metrics,
              'motor_rpm': {'mean': float(np.mean(rpms)), 'minimum': min(rpms), 'maximum': max(rpms)},
              'cpu': {'threads': 4, 'mean_ms': float(np.mean(timings)), 'p95_ms': float(np.percentile(timings, 95)),
                      'includes': 'Hann FFT, features, frozen chart/AE and HI', 'workload': 'All nine services active; conveyor deliberately stopped, diagnostic motor continues'},
              'deployed_health_index_max_abs_delta': max(abs(entry['blind_prediction']['health_index']-entry['health']['health_index']) for entry in captured),
              'restore': restore, 'limitations': ['Synthetic live vibration derived from observed motor state; not measured real bearing damage.',
                                                'Constant approximately 955 RPM, one load/noise setting; weak level 1 is included rather than hidden.']}
    (output/'live-pdm.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


def main():
    """Require explicit actuation authorization and the previously recorded model digest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dashboard-url', default='http://127.0.0.1:8080')
    parser.add_argument('--expected-model-sha256', required=True)
    parser.add_argument('--samples', type=int, default=25, choices=range(20, 26))
    parser.add_argument('--execute-operator-test', action='store_true', required=True)
    args = parser.parse_args()
    collect(args)


if __name__ == '__main__':
    main()
