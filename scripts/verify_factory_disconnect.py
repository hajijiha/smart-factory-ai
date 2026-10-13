"""Verify live broker outage against direct ROS observations and manual recovery."""
import argparse
import json
import shlex
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from scripts.verify_factory_controls import request, wait_for, stopped


def ros_state(container):
    """Read the simulator independently of the deliberately disconnected broker."""
    program = ('import rclpy;from std_msgs.msg import String;rclpy.init();'
               'node=rclpy.create_node("outage_observer");messages=[];'
               'sub=node.create_subscription(String,"/factory/world/state",lambda m:messages.append(m.data),10);'
               '\nwhile not messages: rclpy.spin_once(node,timeout_sec=1)'
               '\nprint(messages[0]);node.destroy_node();rclpy.shutdown()')
    output = subprocess.check_output(['docker', 'exec', container, 'bash', '-lc',
                                      'source /opt/ros/humble/setup.bash; python3 -c '+shlex.quote(program)],
                                     text=True, timeout=15)
    return json.loads(output)


def verify(url, project, output):
    """Stop the test broker, require a physical stop and prevent automatic resume."""
    broker, simulator = project+'-broker-1', project+'-simulator-1'
    report = {'checks': {}, 'observations': {}, 'outage_wall_seconds': 30}
    report['observations']['before'] = wait_for(url, lambda state: state['running'])
    subprocess.run(['docker', 'stop', broker], check=True, stdout=subprocess.DEVNULL)
    try:
        time.sleep(4)
        envelope = request(url, '/api/world')
        report['checks']['api_disconnected_and_stale'] = not envelope['connected'] and envelope['stale']
        first = ros_state(simulator)
        report['observations']['direct_ros_stopped'] = first
        report['checks']['physical_stop_without_mqtt'] = stopped(first)
        try:
            request(url, '/api/reset', {})
            denied = False
        except HTTPError as error:
            denied = error.code in (409, 503)
        report['checks']['offline_reset_denied'] = denied
        time.sleep(26)
        final = ros_state(simulator)
        report['observations']['direct_ros_after_30s'] = final
        report['checks']['outage_stays_stopped'] = stopped(final)
        report['checks']['same_product_identities_during_stop'] = sorted(
            item.get('product_id') for item in first['objects'] if item['kind'] == 'product') == sorted(
            item.get('product_id') for item in final['objects'] if item['kind'] == 'product')
    finally:
        subprocess.run(['docker', 'start', broker], check=True, stdout=subprocess.DEVNULL)
    restored = wait_for(url, stopped, timeout=90)
    time.sleep(5)
    report['observations']['reconnected'] = wait_for(url, stopped)
    report['checks']['reconnect_stays_stopped'] = restored['run_id'] == report['observations']['before']['run_id']
    report['observations']['reset_ack'] = request(url, '/api/reset', {})
    running = wait_for(url, lambda state: state['running'] and any(
        abs(item['velocity']) > 1 for item in state['joints']), timeout=60)
    report['checks']['explicit_manual_resume'] = running['run_id'] == restored['run_id']
    report['passed'] = all(report['checks'].values())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': report['checks']}), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8086')
    parser.add_argument('--project', default='smart-factory-web-20261013')
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    raise SystemExit(0 if verify(arguments.url, arguments.project, arguments.output)['passed'] else 1)
