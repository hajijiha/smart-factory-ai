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
