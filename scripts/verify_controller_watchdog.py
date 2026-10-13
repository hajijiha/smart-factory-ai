"""Prove that independent controller observations latch a paused simulator off."""
import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

from scripts.verify_factory_controls import request, wait_for, stopped, positions


def retained_command(broker):
    """Read the actual retained conveyor command with a bounded broker subscription."""
    payload = subprocess.check_output(['docker', 'exec', broker, 'mosquitto_sub',
        '-h', '127.0.0.1', '-t', 'factory/command/conveyor', '-C', '1', '-W', '5'],
        text=True, timeout=10)
    return json.loads(payload)


def verify(url, project, output):
    """Pause all simulator threads, require a latched stop, then explicitly recover."""
    simulator, broker = project+'-simulator-1', project+'-broker-1'
    report = {'checks':{}, 'observations':{}, 'pause_wall_seconds':15}
    initial = wait_for(url, lambda state:state['running'], timeout=20)
    report['observations']['before'] = initial
    started = datetime.now(timezone.utc)
    subprocess.run(['docker','pause',simulator], check=True, stdout=subprocess.DEVNULL, timeout=10)
    try:
        print('Simulator paused: awaiting independent controller watchdog', flush=True)
        time.sleep(4)
        first = request(url, '/api/world')
        report['observations']['paused_first'] = first
        report['checks']['world_disconnected_and_stale'] = not first['connected'] and first['stale']
        command = retained_command(broker)
        report['observations']['watchdog_command'] = command
        command_time = datetime.fromisoformat(command['timestamp'].replace('Z','+00:00'))
        report['checks']['independent_controller_retained_stop'] = (
            command.get('running') is False and command.get('reason') == 'world_observation_timeout'
            and command.get('run_id') == initial['run_id'] and command_time >= started)
        try:
            request(url, '/api/reset', {})
            denied = False
        except HTTPError as error:
            denied = error.code == 409
        report['checks']['stale_reset_409'] = denied
        time.sleep(max(0, 15-(datetime.now(timezone.utc)-started).total_seconds()))
        final = request(url, '/api/world')
        report['observations']['paused_final'] = final
        report['checks']['cached_positions_and_sequence_frozen'] = (
            first['state']['sequence'] == final['state']['sequence'] and
            positions(first['state']) == positions(final['state']))
        report['checks']['watchdog_stop_retained_through_pause'] = retained_command(broker) == command
    finally:
        subprocess.run(['docker','unpause',simulator], check=True, stdout=subprocess.DEVNULL, timeout=10)
    after = wait_for(url, stopped, timeout=30)
    report['observations']['after_unpause'] = after
    time.sleep(5)
    stable = wait_for(url, stopped, timeout=10)
    report['observations']['fresh_but_latched'] = stable
    report['checks']['fresh_world_never_resumes'] = after['run_id'] == stable['run_id'] == initial['run_id']
    deadline = time.monotonic()+25
    while time.monotonic() < deadline:
        if request(url, '/api/snapshot').get('process',{}).get('reset_ready'):
            break
        time.sleep(.25)
    report['observations']['manual_reset_ack'] = request(url, '/api/reset', {})
    resumed = wait_for(url, lambda state:state['running'] and any(
        abs(joint['velocity']) > 1 for joint in state['joints']), timeout=30)
    report['observations']['manually_resumed'] = resumed
    report['checks']['only_explicit_safe_reset_resumes'] = resumed['run_id'] == initial['run_id']
    report['passed'] = all(report['checks'].values())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'passed':report['passed'], 'checks':report['checks']}), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8086')
    parser.add_argument('--project', default='smart-factory-web-20261013')
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    raise SystemExit(0 if verify(arguments.url, arguments.project, arguments.output)['passed'] else 1)
