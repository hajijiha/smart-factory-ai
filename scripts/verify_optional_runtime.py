"""Observe a real MQTT/Gazebo wear run, recurrent predictions, persisted PSI and alarms."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from scripts.verify_runtime import request, ready
from factory.settings import dashboard_url, verification_path


def verify(base, output, timeout=260):
    """Collect changing wear predictions and fixed-reference drift without injecting feature events."""
    ready(base)
    started = datetime.now(timezone.utc).isoformat()
    rows, alarms, seen = [], {}, set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = request(base, '/api/snapshot')
        prediction = snapshot.get('prognostics')
        if prediction and prediction.get('wear_scenario') and prediction['event_id'] not in seen:
            seen.add(prediction['event_id'])
            rows.append(prediction)
        for alarm in snapshot['alarms']:
            if alarm['source'] == 'drift' and datetime.fromisoformat(str(alarm['timestamp']).replace('Z', '+00:00')) >= datetime.fromisoformat(started):
                alarms[alarm['event_id']] = alarm
        if rows and rows[-1]['wear_scenario']['true_rul_seconds'] == 0 and rows[-1]['drift']['status'] == 'drift' and alarms:
            break
        time.sleep(1)
    estimated = [row for row in rows if row['rul']['status'] == 'estimated']
    evaluation = [row for row in rows if row['drift_evaluated']]
    errors = np.array([row['rul']['rul_seconds'] - row['wear_scenario']['true_rul_seconds'] for row in estimated])
    passed = len(estimated) >= 20 and any(row['drift']['status'] == 'drift' for row in evaluation) and bool(alarms)
    report = {'passed': passed, 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
        'transport': 'Gazebo joint -> generated waveform -> MQTT sensor -> separate PdM -> MQTT health -> separate prognostics -> PostgreSQL -> HTTP',
        'scope': 'One calibrated synthetic monotonic wear trajectory; independent 12-unit test metrics are in rul.json',
        'source_events': len(rows), 'estimated_events': len(estimated), 'drift_evaluations': len(evaluation),
        'online_mae_seconds': float(np.abs(errors).mean()) if len(errors) else None,
        'online_rmse_seconds': float(np.sqrt(np.square(errors).mean())) if len(errors) else None,
        'drift_alarms': list(alarms.values()), 'observations': rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('observations', 'drift_alarms')}, indent=2))
    if not passed:
        raise RuntimeError('Real optional runtime evidence is incomplete; inspect the saved observations')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default=dashboard_url())
    parser.add_argument('--output', type=Path, default=verification_path('optional-runtime.json'))
    parser.add_argument('--timeout', type=int, default=260)
    args = parser.parse_args()
    verify(args.url, args.output, args.timeout)
