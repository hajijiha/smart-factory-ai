"""Fail-closed camera alignment, PUBACK retries and reconnect restart authorization."""
import math
import pytest
from simulator.live_protocol import (ObservationBuffer, AcknowledgedBatch, ResumeLatch,
                                     acknowledged_publish, camera_is_fresh)


def observation(time, sequence, run='run-a', product='unit-a', inspection='inspect-a', x=0., quaternion=None):
    """Represent a measured world, independently of any renderer or defect label."""
    return {'run_id': run, 'sim_time': time, 'sequence': sequence, 'objects': [
        {'id': 'product_01', 'kind': 'product', 'product_id': product, 'inspection_id': inspection,
         'position': [x, 0., .58], 'velocity': [.35, 0., 0.], 'quaternion': quaternion or [0., 0., 0., 1.]}]}


def test_frame_alignment_interpolates_measured_bracket_and_rotation():
    """A camera inside a 90-ms observation bracket uses bounded spatial and rotational interpolation."""
    buffer = ObservationBuffer()
    buffer.append(observation(1., 1, x=0.))
    buffer.append(observation(1.09, 2, x=.09, quaternion=[0., 0., 1., 0.]))
    frame, target, metadata = buffer.align(1.045, 'run-a', 'product_01', 'unit-a', 'inspect-a')
    assert frame['sim_time'] == 1.045
    assert target['position'][0] == pytest.approx(.045)
    assert target['quaternion'][2:] == pytest.approx([math.sqrt(.5), math.sqrt(.5)])
    assert metadata['approximate'] is True and metadata['extrapolated'] is False
    assert metadata['gap_sim_seconds'] <= .10


@pytest.mark.parametrize('time', [.99, 1.11])
def test_frame_alignment_never_extrapolates(time):
    """Frames before/after the observed bracket must wait instead of inventing a product pose."""
    buffer = ObservationBuffer()
    buffer.append(observation(1., 1))
    buffer.append(observation(1.09, 2))
    assert buffer.align(time, 'run-a', 'product_01', 'unit-a', 'inspect-a') is None


def test_gap_recycled_slot_and_retired_run_reject_capture():
    """A large gap or reused physical slot cannot borrow another product's geometry."""
    buffer = ObservationBuffer()
    buffer.append(observation(1., 1))
    buffer.append(observation(1.2, 2))
    assert buffer.align(1.1, 'run-a', 'product_01', 'unit-a', 'inspect-a') is None
    buffer = ObservationBuffer()
    buffer.append(observation(1., 1))
    buffer.append(observation(1.09, 2, product='new-unit'))
    assert buffer.align(1.045, 'run-a', 'product_01', 'unit-a', 'inspect-a') is None
    buffer.append(observation(.1, 1, run='run-b'))
    assert buffer.align(1.045, 'run-a', 'product_01', 'unit-a', 'inspect-a') is None


def test_observation_history_is_bounded_and_rejects_reordered_states():
    """Long sessions retain a finite pose history and never accept an older sequence."""
    buffer = ObservationBuffer(3)
    for index in range(10):
        buffer.append(observation(index*.09, index))
    assert len(buffer.states) == 3
    assert not buffer.append(observation(.5, 4))
    assert len(buffer.states) == 3


def test_camera_receipt_age_bound_rejects_stale_future_and_nonfinite():
    """Even a matching simulation timestamp cannot authorize an old camera buffer."""
    assert camera_is_fresh(10., 8.)
    assert not camera_is_fresh(10., 7.999)
    assert not camera_is_fresh(10., 10.001)
    assert not camera_is_fresh(10., float('nan'))


class FakeInfo:
    """Model accepted, queue-rejected and timeout PUBACK states explicitly."""

    def __init__(self, rc=0, acknowledged=True, error=None):
        """Keep delivery state independent from enqueue return code."""
        self.rc, self.acknowledged, self.error = rc, acknowledged, error
        self.timeouts = []

    def wait_for_publish(self, timeout):
        """Record the bounded wait and optionally simulate a connection failure."""
        self.timeouts.append(timeout)
        if self.error:
            raise self.error

    def is_published(self):
        """Report the actual PUBACK state rather than assuming enqueue means delivery."""
        return self.acknowledged


class FakeClient:
    """Capture immutable MQTT attempts through a controlled reconnect."""

    def __init__(self, infos=(), connected=True):
        """Supply independently controlled acknowledgments for successive attempts."""
        self.infos, self.connected, self.calls = list(infos), connected, []

    def is_connected(self):
        """Expose current transport availability."""
        return self.connected

    def publish(self, topic, payload, qos, retain):
        """Observe exact retried bytes and hand back the chosen delivery result."""
        self.calls.append((topic, payload, qos, retain))
        return self.infos.pop(0)


@pytest.mark.parametrize('info', [FakeInfo(rc=15), FakeInfo(acknowledged=False),
                                 FakeInfo(error=RuntimeError('disconnected'))])
def test_enqueue_or_timeout_never_counts_as_delivery(info):
    """Rejected/full queues and absent PUBACK cannot release an inspected product."""
    client = FakeClient([info])
    assert not acknowledged_publish(client, 'factory/image', '{"event_id":"same"}')
    assert all(value <= 2 for value in info.timeouts)


def test_partial_batch_reconnect_retries_same_image_once_sensor_is_acknowledged():
    """A disconnect after sensor PUBACK retains image bytes and stable identity across retry."""
    batch = AcknowledgedBatch([('factory/sensor', '{"event_id":"same"}', False),
                              ('factory/image', '{"event_id":"same","image":"frozen"}', False)])
    client = FakeClient([FakeInfo(), FakeInfo(acknowledged=False), FakeInfo()])
    assert not batch.deliver(client)
    client.connected = False
    assert not batch.deliver(client)
    assert len(client.calls) == 2
    client.connected = True
    assert batch.deliver(client)
    assert [call[0] for call in client.calls] == ['factory/sensor', 'factory/image', 'factory/image']
    assert client.calls[1][1] == client.calls[2][1]
    assert batch.deliver(client) and len(client.calls) == 3


def test_reconnect_requires_fresh_start_after_physical_stop():
    """A retained old start cannot undo a disconnect stop; a new same-run manual reset can."""
    latch = ResumeLatch()
    latch.stop('2026-10-13T00:00:10Z', 'run-a')
    now = '2026-10-13T00:00:12Z'
    assert not latch.allow_start({'timestamp': '2026-10-13T00:00:09Z'}, now, 'run-a')
    assert not latch.allow_start({'timestamp': '2026-10-13T00:00:11Z', 'run_id': 'retired'}, now, 'run-a')
    assert not latch.allow_start({'timestamp': '2026-10-13T00:00:14Z'}, now, 'run-a')
    assert not latch.allow_start({'timestamp': '2026-10-13T00:00:11'}, now, 'run-a')
    assert latch.allow_start({'timestamp': '2026-10-13T00:00:11Z', 'run_id': 'run-a'}, now, 'run-a')
    assert latch.stopped_at is None
