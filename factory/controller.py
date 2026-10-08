"""Latched MQTT conveyor safety interlock; reset requires a healthy machine."""
import threading
from factory.common import connect, publish, utc_now


def main():
    """Subscribe to health/reset events and command the simulator asynchronously."""
    state = {'latched':False, 'latest_health':None}
    ready = threading.Event()
    def handler(topic, message):
        """Latch danger and reject unsafe or premature resets."""
        ready.wait()
        if topic == 'factory/health':
            state['latest_health'] = message
            if message['status'] == 'danger' and not state['latched']:
                state['latched'] = True
                publish(client, 'factory/command/conveyor', {'running':False, 'timestamp':utc_now(),
                        'reason':'danger_interlock', 'event_id':message['event_id']}, retain=True)
        elif topic == 'factory/command/reset':
            health = state['latest_health']
            if health and health['health_index'] >= 80:
                state['latched'] = False
                publish(client, 'factory/command/conveyor', {'running':True, 'timestamp':utc_now(), 'reason':'manual_safe_reset'}, retain=True)
            else:
                publish(client, 'factory/alarm', {'timestamp':utc_now(), 'source':'controller', 'severity':'warning',
                        'message':'재시작 거절: 정상 상태(HI ≥ 80)를 먼저 확인하세요.'})
    client = connect('controller', ['factory/health', 'factory/command/reset'], handler)
    ready.set()
    threading.Event().wait()


if __name__ == '__main__':
    main()
