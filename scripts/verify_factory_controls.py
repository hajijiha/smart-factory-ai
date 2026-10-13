"""Exercise operator stop, danger interlock and healthy manual recovery on observed poses."""
import argparse
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def request(url, path, data=None):
    """Read an API or send an explicit local operator command."""
    body = json.dumps(data or {}).encode() if data is not None else None
    with urlopen(Request(url+path, data=body, headers={'Content-Type': 'application/json'}), timeout=5) as response:
        return json.load(response)


def wait_for(url, predicate, timeout=45):
    """Await a fresh physical observation satisfying the independent condition."""
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        envelope = request(url, '/api/world')
        if envelope.get('connected') and not envelope.get('stale') and predicate(envelope['state']):
            return envelope['state']
        time.sleep(.15)
    raise TimeoutError('Physical observation condition did not arrive')


def stopped(state):
    """Require an observed operator stop and every physical roller below the stop limit."""
    rollers = [item for item in state['joints'] if item['id'].startswith('roller')]
    return state['running'] is False and rollers and max(abs(item['velocity']) for item in rollers) < .01


def positions(state):
    """Index actual product positions without calculating independent browser motion."""
    return {item['id']: item['position'] for item in state['objects'] if item['kind'] == 'product'}


def verify(url, output):
    """Confirm physical stop, denied dangerous reset, and latched healthy recovery."""
    report = {'checks': {}, 'observations': {}}
    request(url, '/api/fault', {'fault_level': 0})
    start = wait_for(url, lambda state: state['running'] is True)
    report['observations']['initial'] = start
    report['observations']['operator_stop_ack'] = request(url, '/api/stop', {})
    stop = wait_for(url, stopped)
    time.sleep(2)
    settled = wait_for(url, stopped)
    displacement = {key: [b-a for a, b in zip(value, positions(settled)[key])]
                    for key, value in positions(stop).items() if key in positions(settled)}
    report['observations']['operator_stopped'] = stop
    report['observations']['settled_displacement_metres'] = displacement
    report['checks']['operator_stop_observed'] = bool(displacement and all(abs(delta[0]) < .05 for delta in displacement.values()))
    report['observations']['manual_reset_ack'] = request(url, '/api/reset', {})
    restarted = wait_for(url, lambda state: state['running'] and any(abs(item['velocity']) > 1 for item in state['joints']))
    report['checks']['healthy_manual_restart'] = restarted['run_id'] == start['run_id']
    request(url, '/api/fault', {'fault_level': 10})
    danger_stop = wait_for(url, stopped)
    report['observations']['danger_stopped'] = danger_stop
    try:
        request(url, '/api/reset', {})
        denied = False
    except HTTPError as error:
        denied = error.code in (409, 503)
    report['checks']['danger_reset_denied'] = denied
    request(url, '/api/fault', {'fault_level': 0})
    deadline = time.monotonic()+45
    healthy = None
    while time.monotonic() < deadline:
        snapshot = request(url, '/api/snapshot')
        healthy = snapshot.get('latest_health') or {}
        if healthy.get('status') == 'normal' and healthy.get('health_index', 0) >= 80 and snapshot['line']['fault_level'] == 0:
            break
        time.sleep(.3)
    report['observations']['recovered_health'] = healthy
    recovered_stop = wait_for(url, stopped)
    report['checks']['normal_recovery_stays_stopped'] = stopped(recovered_stop)
    request(url, '/api/reset', {})
    final = wait_for(url, lambda state: state['running'] and any(abs(item['velocity']) > 1 for item in state['joints']))
    report['checks']['explicit_recovery_restart'] = final['run_id'] == start['run_id']
    report['passed'] = all(report['checks'].values())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': report['checks']}, ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://dashboard:8080')
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    result = verify(arguments.url.rstrip('/'), arguments.output)
    raise SystemExit(0 if result['passed'] else 1)
