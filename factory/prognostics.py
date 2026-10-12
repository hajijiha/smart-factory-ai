"""Independent MQTT RUL and drift worker consuming diagnosed feature windows."""
import json
import logging
import queue
import time
from collections import deque
from datetime import datetime, timezone
import torch
from factory.common import ARTIFACTS, CONFIG, connect, publish
from factory.pdm.features import NAMES
from factory.pdm.rul import RULPredictor
from factory.pdm.drift import PSIMonitor
from factory.provenance import require_runtime_generator


class Prognostics:
    """Join RUL trends and drift populations while rejecting duplicate or reversed events."""
    def __init__(self, predictor, monitor):
        """Initialize independent model histories and the drift-alarm transition latch."""
        self.predictor, self.monitor = predictor, monitor
        self.recent = deque(maxlen=512)
        self.last_timestamp = None
        self.drifting = False

    def observe(self, message):
        """Process one fresh health event and return metrics plus an optional drift alarm."""
        event_id = message['event_id']
        stamp = datetime.fromisoformat(message['timestamp'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('Prognostics requires timezone-aware event timestamps')
        timestamp = stamp.timestamp()
        if not 0 <= datetime.now(timezone.utc).timestamp() - timestamp <= 5:
            raise ValueError('Prognostics rejects future or stale source observations')
        if event_id in self.recent or self.last_timestamp is not None and timestamp <= self.last_timestamp:
            return None, None
        features = [message['features'][name] for name in NAMES]
        if self.last_timestamp is not None and timestamp - self.last_timestamp > 5:
            self.monitor = PSIMonitor(self.monitor.reference, self.monitor.settings)
            self.drifting = False
        rul = self.predictor.update(features, timestamp)
        evaluated = self.monitor.update(features)
        self.recent.append(event_id)
        self.last_timestamp = timestamp
        result = {'event_id': event_id, 'timestamp': message['timestamp'], 'rul': rul,
                  'drift': self.monitor.latest, 'drift_evaluated': evaluated is not None}
        if 'wear_scenario' in message:
            result['wear_scenario'] = message['wear_scenario']
        alarm = None
        if evaluated is not None:
            drifting = evaluated['status'] == 'drift'
            if drifting and not self.drifting:
                alarm = {'event_id': event_id, 'timestamp': message['timestamp'], 'source': 'drift', 'severity': 'warning',
                         'message': f"학습 분포 변화 감지: PSI {evaluated['psi']:.3f} ≥ {evaluated['threshold']:.2f}. 고장·운전 조건·센서 변화를 확인하세요."}
            self.drifting = drifting
        return result, alarm


def main():
    """Wait for frozen artifacts and publish predictions separately from diagnosis/control."""
    torch.set_num_threads(4)
    paths = (ARTIFACTS / 'rul.pt', ARTIFACTS / 'drift-reference.json')
    while not all(path.is_file() for path in paths):
        logging.info('Waiting for RUL and fixed drift-reference artifacts')
        time.sleep(5)
    reference = json.loads(paths[1].read_text())
    require_runtime_generator(reference)
    worker = Prognostics(RULPredictor(paths[0], CONFIG['rul']['minimum_rms_slope_per_second']), PSIMonitor(reference, CONFIG['drift']))
    events = queue.Queue(maxsize=256)

    def handler(topic, message):
        """Queue diagnosed vibration events without blocking MQTT keepalive."""
        events.put_nowait(message)

    client = connect('prognostics', ['factory/health'], handler)
    while True:
        message = events.get()
        try:
            result, alarm = worker.observe(message)
            if result is not None:
                publish(client, 'factory/prognostics', result)
                if alarm:
                    publish(client, 'factory/alarm', alarm)
                logging.info('RUL %s drift %s', result['rul']['rul_seconds'], result['drift']['status'])
        except (ValueError, TypeError, KeyError):
            logging.exception('Rejected malformed prognostics feature event')


if __name__ == '__main__':
    main()
