"""Independent interlock regressions with no connection to the running MQTT broker."""
from datetime import datetime, timedelta, timezone
import pytest
from factory import controller


class ServiceStopped(Exception):
    """End only the mocked service's final infinite wait."""


class FakeEvent:
    """Model ready notification and stop the service when waiting indefinitely."""
    def __init__(self):
        """Create an unset event."""
        self.ready = False

    def set(self):
        """Mark the event ready."""
        self.ready = True

    def wait(self, timeout=None):
        """Return for a ready event; otherwise end the mocked service."""
        if not self.ready:
            raise ServiceStopped()
        return True


@pytest.fixture
def interlock(monkeypatch):
    """Capture controller callbacks and publications without starting network threads."""
    monkeypatch.setenv('FACTORY_WORLD_MODE', 'dataset')
    callbacks = {}
    published = []

    def connect(client_id, topics, handler):
        """Capture the real controller handler."""
        callbacks['handler'] = handler
        return object()

    def publish(client, topic, message, retain=False):
        """Record commands without contacting MQTT."""
        published.append((topic, message, retain))

    monkeypatch.setattr(controller, 'connect', connect)
    monkeypatch.setattr(controller, 'publish', publish)
    monkeypatch.setattr(controller.threading, 'Event', FakeEvent)
    with pytest.raises(ServiceStopped):
        controller.main()
    return callbacks['handler'], published


def stamp(offset=0):
    """Return a UTC observation timestamp relative to the test time."""
    return (datetime.now(timezone.utc)+timedelta(seconds=offset)).isoformat().replace('+00:00', 'Z')


def healthy(handler, health_offset=0, line_offset=0, hi=95, level=0):
    """Supply independently timed line and health observations."""
    handler('factory/line', {'timestamp':stamp(line_offset),'fault_level':level,'running':False})
    handler('factory/health', {'timestamp':stamp(health_offset),'event_id':'review','status':'normal','health_index':hi})


def test_danger_latches_only_once_and_requires_explicit_safe_reset(interlock):
    """Danger repeatedly commands one stop; healthy telemetry alone never restarts."""
    handler, published = interlock
    danger = {'timestamp':stamp(),'event_id':'danger','status':'danger','health_index':15}
    handler('factory/health', danger)
    handler('factory/health', danger)
    assert len(published) == 1
    assert published[0][0] == 'factory/command/conveyor'
    assert published[0][1]['running'] is False
    assert published[0][2] is True
    healthy(handler)
    assert len(published) == 1
    handler('factory/command/reset', {})
    assert published[-1][1]['running'] is True


def test_explicit_dataset_reset_ignores_retained_live_world(interlock):
    """A retained old operating epoch cannot impose physical gates on a dataset run."""
    handler, published = interlock
    handler('factory/world/state', {'run_id':'old-live-run', 'sequence':1,
            'timestamp':stamp(-30), 'sim_time':1., 'running':True, 'objects':[], 'joints':[]})
    healthy(handler)
    handler('factory/command/reset', {})
    assert published[-1][0] == 'factory/command/conveyor'
    assert published[-1][1]['running'] is True


@pytest.mark.parametrize('health_offset,line_offset,hi,level', [(-20,0,95,0),(0,0,79.99,0),(0,0,95,1)])
def test_reset_rejects_stale_health_low_hi_and_active_fault(interlock, health_offset, line_offset, hi, level):
    """Freshness, score and fault checks each independently reject an unsafe reset."""
    handler, published = interlock
    healthy(handler, health_offset, line_offset, hi, level)
    handler('factory/command/reset', {})
    assert all(not message.get('running', False) for _, message, _ in published)
    assert published[-1][0] == 'factory/alarm'


def test_reset_rejects_future_health_observation(interlock):
    """A future timestamp must not pass the maximum-age freshness check."""
    handler, published = interlock
    healthy(handler, health_offset=60)
    handler('factory/command/reset', {})
    assert all(not message.get('running', False) for _, message, _ in published)


def test_reset_rejects_stale_fault_level_observation(interlock):
    """A current HI must not authorize a restart with an old line/fault observation."""
    handler, published = interlock
    healthy(handler, line_offset=-60)
    handler('factory/command/reset', {})
    assert all(not message.get('running', False) for _, message, _ in published)


@pytest.mark.parametrize('observation', [None, {}, {'timestamp':None}, {'timestamp':'not-a-date'}, {'timestamp':'2026-10-08T12:00:00'}, 'future'])
def test_invalid_or_future_observations_are_not_recent(observation):
    """Missing, malformed, timezone-free and future observations fail closed."""
    if observation == 'future':
        observation = {'timestamp':stamp(60)}
    assert controller.recent(observation) is False


def test_reset_rejects_future_fault_level_observation(interlock):
    """Line observations must independently satisfy the lower age bound too."""
    handler, published = interlock
    healthy(handler, line_offset=60)
    handler('factory/command/reset', {})
    assert all(not message.get('running', False) for _, message, _ in published)


def test_live_direct_mqtt_reset_requires_fresh_world_stop(interlock, monkeypatch):
    """Direct reset messages cannot bypass the live physical-observation guard."""
    monkeypatch.setenv('FACTORY_WORLD_MODE', 'live')
    handler, published = interlock
    healthy(handler)
    handler('factory/command/reset', {})
    assert published[-1][0] == 'factory/alarm'
    assert not any(message.get('running') is True for _, message, _ in published)
    handler('factory/world/state', {'run_id': 'live-a', 'sequence': 0, 'timestamp': stamp(),
            'sim_time': 1.0, 'world_version': 'live-v1', 'running': False, 'objects': [],
            'joints': [{'id': 'roller_01', 'position': 0, 'velocity': 0}]})
    handler('factory/line', {'timestamp': stamp(), 'run_id': 'live-a', 'fault_level': 0,
                           'running': False, 'roller_velocity': 0.0})
    handler('factory/health', {'timestamp': stamp(), 'run_id': 'live-a', 'event_id': 'healthy-live',
                             'health_index': 95, 'status': 'normal'})
    handler('factory/command/reset', {'timestamp': stamp(), 'run_id': 'live-a'})
    assert published[-1][0] == 'factory/command/conveyor'
    assert published[-1][1]['running'] is True


def test_live_direct_mqtt_reset_rejects_stale_world_even_with_new_health(interlock, monkeypatch):
    """Updating HI alone cannot make an old physical world safe to restart."""
    monkeypatch.setenv('FACTORY_WORLD_MODE', 'live')
    handler, published = interlock
    handler('factory/world/state', {'run_id': 'live-a', 'sequence': 0, 'timestamp': stamp(-10),
            'sim_time': 1.0, 'world_version': 'live-v1', 'running': False, 'objects': [], 'joints': []})
    handler('factory/line', {'timestamp': stamp(), 'run_id': 'live-a', 'fault_level': 0,
                           'running': False, 'roller_velocity': 0.0})
    handler('factory/health', {'timestamp': stamp(), 'run_id': 'live-a', 'event_id': 'healthy-live',
                             'health_index': 95, 'status': 'normal'})
    handler('factory/command/reset', {})
    assert published[-1][0] == 'factory/alarm'
    assert not any(message.get('running') is True for _, message, _ in published)


@pytest.mark.parametrize('reset_command', [{}, {'timestamp': 'old'}, {'run_id': 'old-run'}])
def test_live_reset_cannot_replay_old_operator_intent(interlock, monkeypatch, reset_command):
    """Fresh telemetry alone never makes a replayed reset command safe to execute."""
    monkeypatch.setenv('FACTORY_WORLD_MODE', 'live')
    handler, published = interlock
    handler('factory/world/state', {'run_id': 'live-a', 'sequence': 0, 'timestamp': stamp(),
            'sim_time': 1.0, 'world_version': 'live-v1', 'running': False, 'objects': [], 'joints': []})
    handler('factory/line', {'timestamp': stamp(), 'run_id': 'live-a', 'fault_level': 0,
                           'running': False, 'roller_velocity': 0.0})
    handler('factory/health', {'timestamp': stamp(), 'run_id': 'live-a', 'event_id': 'healthy-live',
                             'health_index': 95, 'status': 'normal'})
    message = dict(reset_command)
    if message.get('run_id'):
        message['timestamp'] = stamp()
    handler('factory/command/reset', message)
    assert published[-1][0] == 'factory/alarm'
    assert not any(item.get('running') is True for _, item, _ in published)


@pytest.mark.parametrize('live,armed,connected,stale,reason', [
    (False,True,False,True,None),
    (True,False,False,True,None),
    (True,True,True,False,None),
    (True,True,True,True,'world_observation_timeout'),
    (True,True,False,False,'mqtt_connection_lost'),
])
def test_watchdog_is_armed_only_by_live_world_and_fails_closed(live, armed, connected, stale, reason):
    """Dataset/startup remain compatible; live stale poses and MQTT loss each stop."""
    observation = {'state': {'run_id':'observed'} if armed else None,
                   'mqtt_connected':connected, 'stale':stale}
    latched, actual = controller.watchdog_transition(live, False, observation)
    assert actual == reason
    assert latched is (reason is not None)
    assert observation['mqtt_connected'] is connected


def test_watchdog_latches_once_and_fresh_observations_never_resume():
    """A timeout sends one stop; continued staleness and recovery both keep the latch."""
    stale = {'state':{'run_id':'observed'}, 'mqtt_connected':True, 'stale':True}
    latched, reason = controller.watchdog_transition(True, False, stale)
    assert latched and reason == 'world_observation_timeout'
    assert controller.watchdog_transition(True, latched, stale) == (True, None)
    recovered = {**stale, 'stale':False}
    assert controller.watchdog_transition(True, latched, recovered) == (True, None)


def test_periodic_watchdog_retains_one_stop_through_world_recovery(monkeypatch):
    """The service timer stops without new MQTT input, then fresh poses cannot restart it."""
    from factory.world_state import WorldCache
    base = datetime.now(timezone.utc)
    elapsed = [0.0]
    cache = WorldCache(clock=lambda:elapsed[0], now=lambda:base+timedelta(seconds=elapsed[0]))
    callbacks, published = {}, []
    ticks = [0]

    class ControlledEvent:
        """Advance observations/time only from the controller's periodic wait."""
        def __init__(self):
            """Ready and timer events begin unset."""
            self.ready = False

        def set(self):
            """Release the MQTT initialization guard."""
            self.ready = True

        def wait(self, timeout=None):
            """Simulate fresh, silent timeout, then fresh recovery on separate timer ticks."""
            if self.ready:
                return True
            assert timeout == .25
            ticks[0] += 1
            if ticks[0] == 4:
                raise ServiceStopped()
            if ticks[0] == 2:
                elapsed[0] = 2.5
            elif ticks[0] in (1, 3):
                callbacks['handler']('factory/world/state', {'run_id':'timer-world',
                    'sequence':ticks[0], 'timestamp':(base+timedelta(seconds=elapsed[0])).isoformat(),
                    'sim_time':elapsed[0], 'running':True, 'objects':[], 'joints':[]})
            return False

    class ConnectedClient:
        """Leave MQTT connected while the simulator alone stops sending."""
        def is_connected(self):
            """The pause proof isolates missing world observations."""
            return True

    def connect(client_id, topics, handler):
        """Expose the actual handler to the timer-controlled observations."""
        callbacks['handler'] = handler
        return ConnectedClient()

    monkeypatch.setenv('FACTORY_WORLD_MODE', 'live')
    monkeypatch.setattr(controller, 'WorldCache', lambda:cache)
    monkeypatch.setattr(controller, 'connect', connect)
    monkeypatch.setattr(controller, 'publish', lambda client,topic,message,retain=False:published.append((topic,message,retain)))
    monkeypatch.setattr(controller.threading, 'Event', ControlledEvent)
    with pytest.raises(ServiceStopped):
        controller.main()
    assert len(published) == 1
    topic, message, retain = published[0]
    assert topic == 'factory/command/conveyor' and retain is True
    assert message['running'] is False and message['reason'] == 'world_observation_timeout'
    assert message['run_id'] == 'timer-world'
