"""Ordered live observations, bounded evidence and fail-closed restart regression tests."""
import base64
import copy
from datetime import datetime, timedelta, timezone

import pytest
from factory.world_state import CAMERA_MAX_BYTES, WorldCache


@pytest.fixture
def cache():
    """Use clocks advanced together to make source and local staleness reproducible."""
    reference = {'time': datetime(2026, 10, 13, tzinfo=timezone.utc), 'monotonic': 100.0}
    state = WorldCache(max_products=2, now=lambda: reference['time'], clock=lambda: reference['monotonic'])
    state.reference = reference
    return state


def stamp(cache, offset=0):
    """Format a source timestamp relative to the injected wall clock."""
    return (cache.reference['time'] + timedelta(seconds=offset)).isoformat()


def world(cache, run='run-a', sequence=0, **changes):
    """Construct an observed stopped conveyor with a real product and roller pose."""
    message = {'run_id': run, 'sequence': sequence, 'timestamp': stamp(cache), 'sim_time': 2.0,
               'world_version': 'live-v1', 'running': False,
               'objects': [{'id': 'product_01', 'kind': 'product', 'product_id': 'part-1',
                            'position': [0, 0, 1], 'quaternion': [0, 0, 0, 1],
                            'velocity': [0, 0, 0], 'stage': 'transport', 'inspection_id': 'inspection-1'}],
               'joints': [{'id': 'roller_01', 'position': 1.2, 'velocity': 0.0}]}
    message.update(changes)
    return message


def product(cache, phase='entered', part='part-1', **changes):
    """Construct an identified event without simulator defect truth labels."""
    message = {'run_id': 'run-a', 'product_id': part, 'inspection_id': 'inspection-' + part,
               'phase': phase, 'timestamp': stamp(cache), 'sim_time': 2.0}
    message.update(changes)
    return message


def healthy_stopped(cache):
    """Supply separate fresh health, line and physical observations."""
    cache.observe('factory/world/state', world(cache))
    cache.observe('factory/health', {'run_id': 'run-a', 'timestamp': stamp(cache), 'health_index': 95, 'status': 'normal'})
    cache.observe('factory/line', {'run_id': 'run-a', 'timestamp': stamp(cache), 'fault_level': 0,
                                  'running': False, 'roller_velocity': 0.0})


def test_duplicate_reordered_and_retired_run_cannot_replace_pose(cache):
    """New epochs reset local frames; delayed old epochs cannot resurrect motion."""
    assert cache.observe('factory/world/state', world(cache, sequence=5))
    assert not cache.observe('factory/world/state', world(cache, sequence=5))
    assert not cache.observe('factory/world/state', world(cache, sequence=4))
    assert cache.observe('factory/world/state', world(cache, run='run-b', sequence=0))
    assert not cache.observe('factory/world/state', world(cache, sequence=99))
    assert cache.snapshot(True)['state']['run_id'] == 'run-b'
    assert cache.snapshot(True)['rejected_messages'] == 3


@pytest.mark.parametrize('changes', [
    {'sequence': True}, {'running': 1}, {'timestamp': '2026-10-13T00:00:00'},
    {'sim_time': float('nan')}, {'objects': [{'id': 'p', 'position': [1, 2, 3]}]},
])
def test_invalid_world_never_turns_into_valid_observation(cache, changes):
    """Malformed numeric/pose fields and timezone-free data fail validation."""
    assert not cache.observe('factory/world/state', world(cache, **changes))
    assert cache.snapshot(True)['state'] is None
    assert cache.snapshot(True)['stale']


def test_cache_does_not_expose_truth_and_snapshot_cannot_mutate_pose(cache):
    """The web receives an isolated copy with only observation fields."""
    message = world(cache)
    message['objects'][0]['ground_truth_class'] = 2
    assert cache.observe('factory/world/state', message)
    snapshot = cache.snapshot(True)
    assert 'ground_truth_class' not in snapshot['state']['objects'][0]
    snapshot['state']['objects'][0]['position'][0] = 100
    assert cache.snapshot(True)['state']['objects'][0]['position'][0] == 0


def test_source_and_local_receipt_both_expire_without_fabricating_pose(cache):
    """Reconnect alone cannot refresh a stale source or move the final coordinates."""
    cache.observe('factory/world/state', world(cache))
    assert cache.snapshot(True)['connected']
    cache.reference['monotonic'] += 3
    assert cache.snapshot(True)['stale']
    assert cache.snapshot(True)['state']['objects'][0]['position'] == [0, 0, 1]
    assert not cache.snapshot(False)['connected']
    assert cache.snapshot(True)['stale']


@pytest.mark.parametrize('wall_shift', [-.028, -.045, -3600, 3600])
def test_accepted_world_camera_and_reset_survive_wall_clock_correction(cache, wall_shift):
    """UTC corrections after receipt cannot make physically fresh observations stale."""
    healthy_stopped(cache)
    frame = {'run_id':'run-a', 'timestamp':stamp(cache), 'sim_time':1.0,
             'image_base64':base64.b64encode(b'\xff\xd8clock-fixture\xff\xd9').decode()}
    assert cache.observe('factory/camera/inspection', frame)
    cache.reference['time'] += timedelta(seconds=wall_shift)
    cache.reference['monotonic'] += .146
    snapshot = cache.snapshot(True)
    assert snapshot['connected'] and not snapshot['stale']
    assert snapshot['age_ms'] == snapshot['cameras']['inspection']['age_ms'] == 146
    assert not snapshot['cameras']['inspection']['stale']
    assert cache.reset_readiness()[0]
    # UTC remains corrected, but the same accepted observations still expire at two seconds.
    cache.reference['monotonic'] = 102.0001
    assert cache.snapshot(True)['stale']
    assert cache.snapshot(True)['cameras']['inspection']['stale']
    assert not cache.reset_readiness()[0]


@pytest.mark.parametrize('topic', ['factory/world/state', 'factory/camera/inspection',
                                  'factory/health', 'factory/line'])
@pytest.mark.parametrize('future_seconds', [.001, 60])
def test_genuinely_future_stamps_at_receipt_never_enter_freshness_cache(cache, topic, future_seconds):
    """No skew allowance lets a future world/frame/diagnosis authorize live recovery."""
    message = world(cache, timestamp=stamp(cache, future_seconds))
    if topic.startswith('factory/camera/'):
        message['image_base64'] = base64.b64encode(b'\xff\xd8future-fixture\xff\xd9').decode()
    message.update(health_index=95, status='normal', fault_level=0, roller_velocity=0)
    assert not cache.observe(topic, message)
    assert cache.world is None and cache.health is None and cache.line is None
    assert cache.camera('inspection') is None
    assert cache.snapshot(True)['stale'] and not cache.reset_readiness()[0]


def test_future_replacement_cannot_refresh_a_valid_world_receipt(cache):
    """Rejecting a future sequence preserves the old pose and its original expiry."""
    assert cache.observe('factory/world/state', world(cache))
    assert not cache.observe('factory/world/state', world(cache, sequence=1, timestamp=stamp(cache, 60)))
    assert cache.snapshot(True)['state']['sequence'] == 0
    cache.reference['monotonic'] += 2.0001
    assert cache.snapshot(True)['stale']


def test_source_delay_and_elapsed_time_share_the_strict_two_second_budget(cache):
    """A delayed packet spends its remaining source freshness after receipt."""
    assert cache.observe('factory/world/state', world(cache, timestamp=stamp(cache, -1.5)))
    cache.reference['time'] -= timedelta(seconds=100)
    cache.reference['monotonic'] += .5
    assert not cache.snapshot(True)['stale'] and cache.snapshot(True)['age_ms'] == 2000
    cache.reference['monotonic'] += .0001
    assert cache.snapshot(True)['stale']


@pytest.mark.parametrize('delayed_topic', ['factory/health', 'factory/line'])
def test_healthy_reset_requires_source_delay_plus_elapsed_below_five_seconds(cache, delayed_topic):
    """A current world cannot refresh delayed health/fault observations after UTC backsteps."""
    assert cache.observe('factory/world/state', world(cache, timestamp=stamp(cache, -5)))
    assert cache.observe('factory/world/state', world(cache, sequence=1))
    health = {'run_id':'run-a', 'timestamp':stamp(cache), 'health_index':95, 'status':'normal'}
    line = {'run_id':'run-a', 'timestamp':stamp(cache), 'fault_level':0, 'running':False, 'roller_velocity':0}
    (health if delayed_topic.endswith('health') else line)['timestamp'] = stamp(cache, -4.9)
    assert cache.observe('factory/health', health) and cache.observe('factory/line', line)
    assert cache.reset_readiness()[0]
    cache.reference['time'] -= timedelta(seconds=.045)
    cache.reference['monotonic'] += .2
    assert not cache.snapshot(True)['stale']
    ready, reason = cache.reset_readiness()
    assert not ready and '5초' in reason


def test_monotonic_clock_failure_fails_closed(cache):
    """A negative elapsed receipt age cannot silently extend a freshness budget."""
    healthy_stopped(cache)
    cache.reference['monotonic'] -= .01
    assert cache.snapshot(True)['stale'] and cache.snapshot(True)['age_ms'] is None
    assert not cache.reset_readiness()[0]


def test_camera_rejects_invalid_oversize_reordered_and_wrong_run_frames(cache):
    """Only bounded JPEG envelopes for known cameras reach the HTTP stream."""
    cache.observe('factory/world/state', world(cache))
    envelope = {'run_id': 'run-a', 'timestamp': stamp(cache), 'sim_time': 1.0,
                'image_base64': base64.b64encode(b'\xff\xd8jpeg-fixture\xff\xd9').decode()}
    assert cache.observe('factory/camera/overview', envelope)
    assert not cache.observe('factory/camera/overview', envelope)
    invalid = {**envelope, 'timestamp': stamp(cache, .01), 'image_base64': '!notbase64!'}
    assert not cache.observe('factory/camera/overview', invalid)
    assert not cache.observe('factory/camera/unexpected', envelope)
    assert not cache.observe('factory/camera/inspection', {**envelope, 'run_id': 'run-b'})
    assert not cache.observe('factory/camera/inspection', {**envelope, 'image_base64': 'a' * (CAMERA_MAX_BYTES * 2)})
    assert cache.camera('overview')['bytes'] == b'\xff\xd8jpeg-fixture\xff\xd9'
    assert 'bytes' not in cache.snapshot(True)['cameras']['overview']


def test_product_unknown_until_actual_quality_and_late_phases_do_not_rewind(cache):
    """Lifecycle alone does not classify, and delayed entry cannot undo outfeed."""
    event = product(cache, phase='outfeed', position=[2, .45, .58], outfeed_lane='reject', classification='defective')
    assert cache.observe('factory/inspection/event', event)
    assert cache.product_snapshot()[0]['outcome'] == 'unknown'
    assert cache.product_snapshot()[0]['outfeed_position'] == [2, .45, .58]
    assert cache.product_snapshot()[0]['outfeed_lane'] == 'reject'
    assert cache.observe('factory/inspection/event', {**event, 'phase': 'entered'})
    assert cache.product_snapshot()[0]['stage'] == 'outfeed'
    quality = {**event, 'event_id': 'sensor-1', 'detections': [], 'camera_timestamp': stamp(cache),
               'camera_sim_time': 1.75, 'model_version': 'sha256:actual'}
    assert cache.observe('factory/quality', quality)
    entry = cache.product_snapshot()[0]
    assert entry['outcome'] == 'normal'
    assert entry['camera_sim_time'] == 1.75
    assert entry['sensor_event_id'] == 'sensor-1'


def test_cam_and_quality_reconcile_same_identity_only_and_products_are_bounded(cache):
    """CAM-first ordering works without cross-product or cross-inspection mixing."""
    event = product(cache, event_id='sensor-1')
    assert cache.observe('factory/gradcam', {**event, 'gradcam_path': 'inspections/sensor-1_cam.jpg'})
    assert not cache.observe('factory/quality', {**event, 'event_id': 'other-event', 'detections': []})
    assert cache.observe('factory/quality', {**event, 'detections': [{'defect_type': 'dent'}]})
    assert cache.product_snapshot()[0]['outcome'] == 'defective'
    assert not cache.observe('factory/quality', {**event, 'product_id': 'wrong-product', 'detections': []})
    for identifier in ['part-2', 'part-3']:
        assert cache.observe('factory/inspection/event', product(cache, part=identifier))
    assert len(cache.product_snapshot()) == 2
    assert all(entry['product_id'] != 'part-1' for entry in cache.product_snapshot())


def test_reset_requires_fresh_physical_stop_and_cannot_reuse_old_run_health(cache):
    """Healthy scores cannot restart stale or moving rollers or a new simulator epoch."""
    healthy_stopped(cache)
    assert cache.reset_readiness()[0]
    moving = world(cache, sequence=1)
    moving['joints'][0]['velocity'] = .2
    cache.observe('factory/world/state', moving)
    assert not cache.reset_readiness()[0]
    cache.observe('factory/world/state', world(cache, sequence=2))
    assert cache.reset_readiness()[0]
    cache.reference['time'] += timedelta(seconds=3)
    cache.reference['monotonic'] += 3
    assert not cache.reset_readiness()[0]
    cache.observe('factory/world/state', world(cache, run='run-b'))
    assert not cache.reset_readiness()[0]


def test_camera_frames_and_safety_observations_clear_on_new_epoch(cache):
    """A restart never displays prior-run camera footage as the new observation."""
    healthy_stopped(cache)
    frame = {'timestamp': stamp(cache), 'sim_time': 1.0, 'run_id': 'run-a',
             'image_base64': base64.b64encode(b'\xff\xd8fixture\xff\xd9').decode()}
    cache.observe('factory/camera/inspection', frame)
    cache.observe('factory/world/state', world(cache, run='run-b'))
    assert cache.camera('inspection') is None
    assert cache.health is None and cache.line is None


@pytest.mark.parametrize('status', [None, 'unknown', 'danger'])
def test_reset_does_not_treat_unknown_health_as_normal(cache, status):
    """A recent numeric score cannot authorize reset without a valid normal diagnosis."""
    healthy_stopped(cache)
    cache.reference['time'] += timedelta(seconds=.1)
    cache.reference['monotonic'] += .1
    cache.observe('factory/health', {'run_id': 'run-a', 'timestamp': stamp(cache),
                  'health_index': 95, 'status': status})
    assert not cache.reset_readiness()[0]
