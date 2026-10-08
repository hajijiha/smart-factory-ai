"""Latched MQTT conveyor safety interlock; reset requires a healthy machine."""
import threading
from datetime import datetime, timezone
from factory.common import connect, publish, utc_now


def recent(message):
    """Require a valid UTC observation from the last five seconds; fail closed."""
    try:
        stamp = datetime.fromisoformat(message['timestamp'].replace('Z','+00:00'))
        age = (datetime.now(timezone.utc)-stamp).total_seconds()
        return 0 <= age <= 5
    except (TypeError, KeyError, ValueError, AttributeError):
        return False


def main():
    """Subscribe to health/reset events and command the simulator asynchronously."""
    state = {'latched':False, 'latest_health':None, 'latest_line':None}
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
        elif topic == 'factory/line':
            state['latest_line'] = message
        elif topic == 'factory/command/reset':
            health = state['latest_health']
            line = state['latest_line']
            if recent(health) and health['health_index'] >= 80 and recent(line) and line['fault_level'] == 0:
                state['latched'] = False
                publish(client, 'factory/command/conveyor', {'running':True, 'timestamp':utc_now(), 'reason':'manual_safe_reset'}, retain=True)
            else:
                publish(client, 'factory/alarm', {'timestamp':utc_now(), 'source':'controller', 'severity':'warning',
                        'message':'재시작 거절: 레벨 0과 최근 5초 이내 정상 상태(HI ≥ 80)를 먼저 확인하세요.'})
    client = connect('controller', ['factory/health', 'factory/line', 'factory/command/reset'], handler)
    ready.set()
    threading.Event().wait()


if __name__ == '__main__':
    main()
