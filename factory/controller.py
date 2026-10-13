"""Latched MQTT conveyor safety interlock; reset requires a healthy machine."""
import threading
import os
import logging
from datetime import datetime, timezone
from factory.common import connect, publish, utc_now
from factory.world_state import WorldCache, timestamp


def recent(message):
    """Require a valid UTC observation from the last five seconds; fail closed."""
    try:
        stamp = datetime.fromisoformat(message['timestamp'].replace('Z','+00:00'))
        age = (datetime.now(timezone.utc)-stamp).total_seconds()
        return 0 <= age <= 5
    except (TypeError, KeyError, ValueError, AttributeError):
        return False


def watchdog_transition(live, latched, observation):
    """Return the next latch and one stop reason, without mutating state or sending commands.

    A world must have been observed before arming. A recovered connection or
    fresh world cannot release an existing latch; only the guarded reset can.
    """
    if not live or latched or not observation.get('state'):
        return latched, None
    if not observation.get('mqtt_connected'):
        return True, 'mqtt_connection_lost'
    if observation.get('stale'):
        return True, 'world_observation_timeout'
    return latched, None


def main():
    """Handle MQTT interlocks and independently watch live observations every 250 ms."""
    state = {'latched':False, 'latest_health':None, 'latest_line':None}
    lock = threading.RLock()
    world = WorldCache()
    ready = threading.Event()
    def handler(topic, message):
        """Latch danger and reject unsafe or premature resets."""
        ready.wait()
        with lock:
            world.observe(topic, message)
            if topic == 'factory/health':
                state['latest_health'] = message
                if message['status'] == 'danger' and not state['latched']:
                    state['latched'] = True
                    publish(client, 'factory/command/conveyor', {'running':False, 'timestamp':utc_now(),
                            'reason':'danger_interlock', 'event_id':message['event_id']}, retain=True)
            elif topic == 'factory/line':
                state['latest_line'] = message
            elif topic == 'factory/command/reset':
                health = state['latest_health']
                line = state['latest_line']
                mode = os.environ.get('FACTORY_WORLD_MODE')
                live = mode == 'live' or (mode != 'dataset' and bool(world.world))
                physical_ready = world.reset_readiness()[0] if live else True
                command_ready = not live or (recent(message) and (not message.get('run_id') or
                                world.world and message['run_id'] == world.world['run_id']))
                if recent(health) and health['health_index'] >= 80 and recent(line) and line['fault_level'] == 0 and physical_ready and command_ready:
                    state['latched'] = False
                    publish(client, 'factory/command/conveyor', {'running':True, 'timestamp':utc_now(), 'reason':'manual_safe_reset'}, retain=True)
                else:
                    publish(client, 'factory/alarm', {'timestamp':utc_now(), 'source':'controller', 'severity':'warning',
                            'message':'재시작 거절: 레벨 0·최근 정상 상태(HI ≥ 80)와 실제 정지 관측을 먼저 확인하세요.'})
    client = connect('controller', ['factory/health', 'factory/line', 'factory/world/state', 'factory/command/reset'], handler)
    ready.set()
    stopped = threading.Event()
    while not stopped.wait(.25):
        with lock:
            # This thread keeps running even if the simulator's bridge is paused.
            mode = os.environ.get('FACTORY_WORLD_MODE')
            live = mode == 'live' or (mode != 'dataset' and bool(world.world))
            observation = world.snapshot(client.is_connected())
            state['latched'], reason = watchdog_transition(live, state['latched'], observation)
            if reason:
                details = {key:observation[key] for key in
                           ('age_ms','received_at','mqtt_connected','connected','stale','rejected_messages')}
                details.update(sequence=observation['state']['sequence'],
                    source_timestamp=observation['state']['timestamp'],
                    source_age_ms=round((world.now()-timestamp(observation['state']['timestamp'])).total_seconds()*1000, 3),
                    receipt_age_ms=round((world.clock()-world.received)*1000, 3),
                    source_delay_ms_at_receipt=round(world.world_receipt.source_delay*1000, 3),
                    effective_source_age_ms=observation['age_ms'],
                    age_method='source_delay_at_receipt_plus_monotonic_elapsed')
                command = {'running':False, 'timestamp':utc_now(), 'reason':reason,
                           'run_id':observation['state']['run_id'], 'watchdog_observation':details}
                logging.warning('Independent world watchdog latched stop: %s', command)
                publish(client, 'factory/command/conveyor', command, retain=True)


if __name__ == '__main__':
    main()
