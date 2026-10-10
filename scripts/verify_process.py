"""Verify process-view semantics against the real running simulation and database."""
import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from scripts.verify_runtime import ready, request, wait


def equipment(snapshot, name):
    """Find a displayed equipment state by its stable identifier."""
    return next(item for item in snapshot['process']['equipment'] if item['id'] == name)


def observation(snapshot):
    """Keep the observed process state and identifiers without repeating waveform data."""
    health = snapshot['latest_health']
    return {'server_timestamp': snapshot['timestamp'], 'process': snapshot['process'],
            'line': snapshot['line'], 'counts': snapshot['counts'],
            'health': {key: health.get(key) for key in ('event_id', 'timestamp', 'status', 'health_index')},
            'inspection_events': snapshot['inspection_events']}


def verify(base, output):
    """Exercise danger, held inspection, rejected reset and explicit measured recovery."""
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'health': ready(base)}
    try:
        request(base, '/api/fault', {'fault_level': 0})
        wait(base, lambda data: data['latest_health']['health_index'] >= 80 and data['line']['fault_level'] == 0)
        request(base, '/api/reset', {})
        normal = wait(base, lambda data: data['process']['state'] == 'running'
                      and equipment(data, 'conveyor')['state'] == 'running' and data['inspection_events'])
        report['normal'] = observation(normal)
        before = normal['counts']['inspections']
        wait(base, lambda data: data['counts']['inspections'] > before)

        request(base, '/api/fault', {'fault_level': 10})
        danger = wait(base, lambda data: equipment(data, 'motor')['state'] == 'danger'
                      and equipment(data, 'conveyor')['state'] == 'stopped'
                      and equipment(data, 'camera')['state'] == 'stopped')
        assert danger['process']['state'] == 'stopped'
        assert not danger['process']['reset_ready']
        assert abs(danger['line']['roller_velocity']) <= .01
        report['danger_stop'] = observation(danger)
        image_id = danger['inspection_events'][0]['event_id']
        reset = request(base, '/api/reset', {})
        assert reset['accepted']  # Queued request; the controller still decides.
        rejected = wait(base, lambda data: any(alarm['source'] == 'controller' and
                       datetime.fromisoformat(str(alarm['timestamp']).replace('Z', '+00:00')) >
                       datetime.fromisoformat(danger['timestamp'].replace('Z', '+00:00'))
                       for alarm in data['alarms']))
        time.sleep(6)
        held = request(base, '/api/snapshot')
        assert not held['line']['running'] and equipment(held, 'camera')['state'] == 'stopped'
        assert held['inspection_events'][0]['event_id'] == image_id
        assert not equipment(held, 'camera')['fresh']
        report['unsafe_reset_rejected'] = observation(rejected)
        report['held_inspection'] = observation(held)

        events = held['inspection_events']
        assert len({item['event_id'] for item in events}) == len(events) <= 12
        assert sum(len(item['detections']) for item in events) == sum(
            row['defect_type'] != 'normal' for row in held['inspections'])
        path = events[0]['image_path']
        with urllib.request.urlopen(base + '/images/' + path, timeout=10) as response:
            image = response.read()
            assert response.status == 200 and response.headers['Content-Type'].startswith('image/')
            assert image[:2] == b'\xff\xd8'
        report['images'] = {'unique_recent_events': len(events), 'latest_event_id': image_id,
                            'latest_jpeg_bytes': len(image), 'all_detection_rows_grouped': True}

        request(base, '/api/fault', {'fault_level': 0})
        recovered_stopped = wait(base, lambda data: data['process']['reset_ready'])
        assert not recovered_stopped['line']['running']
        report['recovered_stopped'] = observation(recovered_stopped)
        request(base, '/api/reset', {})
        resumed = wait(base, lambda data: data['process']['state'] == 'running'
                       and data['counts']['inspections'] > held['counts']['inspections'])
        assert abs(resumed['line']['roller_velocity']) > .5
        report['resumed'] = observation(resumed)
        report['passed'] = True
    finally:
        request(base, '/api/fault', {'fault_level': 0})
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Process runtime verified: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://localhost:8080')
    parser.add_argument('--output', type=Path, default=Path('docs/results/process-control/runtime.json'))
    arguments = parser.parse_args()
    verify(arguments.url.rstrip('/'), arguments.output)
