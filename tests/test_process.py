"""Process display regressions for delayed telemetry and real interlock conditions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math

import pytest

from factory.process import build_process_snapshot, group_inspections


NOW = datetime(2026, 10, 10, 6, 0, 0, tzinfo=timezone.utc)


def stamp(age=0):
    """Generate independently timed observations relative to a fixed clock."""
    return (NOW-timedelta(seconds=age)).isoformat().replace('+00:00', 'Z')


def snapshot(*, running=True, hi=95, level=0, velocity=None):
    """Represent actual service envelopes without starting MQTT or a database."""
    return {
        'mqtt_connected': True,
        'rows': [{'timestamp': stamp(1), 'event_id': 'sensor-new',
                  'vibration_x': .05, 'temperature': 35, 'rpm': 480}],
        'latest_health': {'timestamp': stamp(1), 'event_id': 'sensor-new',
                          'health_index': hi, 'status': 'normal' if hi >= 80 else 'danger',
                          'inference_ms': 1.5},
        'line': {'timestamp': stamp(1), 'running': running, 'fault_level': level,
                 'roller_velocity': (4 if running else 0) if velocity is None else velocity,
                 'motor_rpm': 480},
        'inspections': [{'event_id': 'image-new', 'timestamp': stamp(1),
                         'image_path': 'inspections/image-new.jpg', 'defect_type': 'normal',
                         'confidence': 1, 'bbox': None, 'gradcam_path': None,
                         'health_index_at_time': hi, 'inference_ms': 15}],
        'alarms': [],
    }


def equipment(result, name):
    """Find a process node by its stable equipment identity."""
    return next(item for item in result['equipment'] if item['id'] == name)


def test_empty_history_is_waiting_and_never_reset_ready():
    result = build_process_snapshot({'mqtt_connected': True}, now=NOW)
    assert result['state'] == 'waiting'
    assert result['reset_ready'] is False
    assert {item['id'] for item in result['equipment']} == {'motor', 'conveyor', 'camera'}
    assert all(item['state'] == 'waiting' for item in result['equipment'])


def test_normal_requires_measured_motion_and_does_not_offer_reset():
    result = build_process_snapshot(snapshot(), now=NOW)
    assert result['state'] == 'running'
    assert equipment(result, 'motor')['state'] == 'normal'
    assert equipment(result, 'conveyor')['state'] == 'running'
    assert result['reset_ready'] is False


def test_danger_does_not_fabricate_an_already_stopped_conveyor():
    data = snapshot(hi=15, level=10)
    result = build_process_snapshot(data, now=NOW)
    assert result['state'] == 'attention'
    assert equipment(result, 'motor')['state'] == 'danger'
    assert equipment(result, 'conveyor')['state'] == 'running'
    assert result['reset_ready'] is False


@pytest.mark.parametrize('running,velocity', [(True, 0), (True, .25), (False, 4), (False, -.2)])
def test_command_and_measured_speed_disagreement_is_a_transition(running, velocity):
    result = build_process_snapshot(snapshot(running=running, velocity=velocity), now=NOW)
    assert equipment(result, 'conveyor')['state'] == 'warning'
    assert result['state'] == 'attention'


def test_recovered_health_does_not_automatically_restart():
    result = build_process_snapshot(snapshot(running=False), now=NOW)
    assert result['state'] == 'stopped'
    assert equipment(result, 'conveyor')['state'] == 'stopped'
    assert result['reset_ready'] is True


@pytest.mark.parametrize('key', ['latest_health', 'line'])
@pytest.mark.parametrize('timestamp', [stamp(5.001), stamp(-1), 'bad-date',
                                      '2026-10-10T06:00:00', None])
def test_reset_independently_rejects_bad_health_and_line_times(key, timestamp):
    data = snapshot(running=False)
    data[key]['timestamp'] = timestamp
    result = build_process_snapshot(data, now=NOW)
    assert result['reset_ready'] is False
    assert result['reset_reason']


def test_exact_five_second_observations_satisfy_controller_boundary():
    data = snapshot(running=False)
    data['latest_health']['timestamp'] = stamp(5)
    data['line']['timestamp'] = stamp(5)
    result = build_process_snapshot(data, now=NOW)
    assert result['reset_ready'] is True


@pytest.mark.parametrize('hi,level', [(79.99, 0), (95, 1), (80, 10)])
def test_reset_rejects_low_health_or_nonzero_fault(hi, level):
    assert build_process_snapshot(snapshot(running=False, hi=hi, level=level),
                                  now=NOW)['reset_ready'] is False


def test_reset_health_threshold_is_inclusive():
    assert build_process_snapshot(snapshot(running=False, hi=80), now=NOW)['reset_ready'] is True


def test_disconnected_mqtt_never_allows_a_reset():
    data = snapshot(running=False)
    data['mqtt_connected'] = False
    result = build_process_snapshot(data, now=NOW)
    assert result['reset_ready'] is False


@pytest.mark.parametrize('key', ['latest_health', 'line'])
def test_missing_service_observation_never_allows_a_reset(key):
    data = snapshot(running=False)
    data[key] = None
    assert build_process_snapshot(data, now=NOW)['reset_ready'] is False


def test_old_sensor_cannot_make_motor_look_healthy_with_a_new_health_record():
    data = snapshot()
    data['rows'][0]['timestamp'] = stamp(20)
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'motor')['state'] == 'stale'
    assert equipment(result, 'motor')['fresh'] is False


def test_retained_running_state_cannot_make_a_stale_conveyor_live():
    data = snapshot()
    data['line']['timestamp'] = stamp(60)
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'conveyor')['state'] == 'stale'
    assert result['state'] == 'stale'
    assert result['reset_ready'] is False


@pytest.mark.parametrize('value', [None, 'false', 0])
def test_invalid_running_observation_cannot_claim_a_physical_stop(value):
    data = snapshot(running=False)
    data['line']['running'] = value
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'conveyor')['state'] == 'stale'
    assert result['reset_ready'] is False


@pytest.mark.parametrize('value', [None, math.nan, math.inf])
def test_absent_or_invalid_roller_speed_is_not_a_confirmed_stop(value):
    data = snapshot(running=False)
    data['line']['roller_velocity'] = value
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'conveyor')['state'] == 'stale'
    assert result['reset_ready'] is False


def test_camera_holds_last_inspection_when_the_physical_line_is_stopped():
    data = snapshot(running=False, hi=15, level=10)
    data['inspections'][0]['timestamp'] = stamp(60)
    result = build_process_snapshot(data, now=NOW)
    camera = equipment(result, 'camera')
    assert camera['state'] == 'stopped'
    assert datetime.fromisoformat(camera['observed_at']) == datetime.fromisoformat(data['inspections'][0]['timestamp'].replace('Z', '+00:00'))
    assert camera['reason']


def test_camera_delays_are_visible_when_the_line_is_running():
    data = snapshot()
    data['inspections'][0]['timestamp'] = stamp(60)
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'camera')['state'] == 'stale'


@pytest.mark.parametrize('value', [None, math.nan, math.inf, -1, 101])
def test_invalid_health_cannot_authorize_reset(value):
    data = snapshot(running=False)
    data['latest_health']['health_index'] = value
    assert build_process_snapshot(data, now=NOW)['reset_ready'] is False


def test_datetime_database_values_and_iso_json_values_have_same_freshness():
    data = snapshot(running=False)
    expected = build_process_snapshot(data, now=NOW)
    for item in [data['rows'][0], data['latest_health'], data['line'], data['inspections'][0]]:
        item['timestamp'] = NOW-timedelta(seconds=1)
    actual = build_process_snapshot(data, now=NOW)
    assert actual['state'] == expected['state']
    assert actual['reset_ready'] == expected['reset_ready']
    assert [item['fresh'] for item in actual['equipment']] == [item['fresh'] for item in expected['equipment']]


def test_multi_detection_history_is_one_event_without_losing_boxes():
    rows = [
        {'id': 'new-0', 'event_id': 'new', 'timestamp': stamp(1),
         'image_path': 'inspections/new.jpg', 'defect_type': 'scratch',
         'confidence': .9, 'bbox': [10, 20, 40, 50],
         'gradcam_path': 'inspections/new_gradcam.jpg', 'health_index_at_time': 90},
        {'id': 'old-0', 'event_id': 'old', 'timestamp': stamp(3),
         'image_path': 'inspections/old.jpg', 'defect_type': 'normal',
         'confidence': 1, 'bbox': None, 'gradcam_path': None, 'health_index_at_time': None},
        {'id': 'new-1', 'event_id': 'new', 'timestamp': stamp(1),
         'image_path': 'inspections/new.jpg', 'defect_type': 'dent',
         'confidence': .8, 'bbox': [100, 120, 150, 170],
         'gradcam_path': 'inspections/new_gradcam.jpg', 'health_index_at_time': 90},
    ]
    before = deepcopy(rows)
    grouped = group_inspections(rows)
    assert rows == before, 'Grouping must not mutate database rows'
    assert [item['event_id'] for item in grouped] == ['new', 'old']
    assert {item['defect_type'] for item in grouped[0]['detections']} == {'scratch', 'dent'}
    assert {tuple(item['bbox']) for item in grouped[0]['detections']} == {(10, 20, 40, 50), (100, 120, 150, 170)}
    assert grouped[1]['detections'] == []
    assert grouped[0]['gradcam_path'] == 'inspections/new_gradcam.jpg'
    assert grouped[1]['gradcam_path'] is None


def test_grouped_history_accepts_database_datetime_ordering():
    rows = [{'id': 'older-0', 'event_id': 'older', 'timestamp': NOW-timedelta(seconds=3),
             'image_path': 'older.jpg', 'defect_type': 'normal'},
            {'id': 'newer-0', 'event_id': 'newer', 'timestamp': NOW-timedelta(seconds=1),
             'image_path': 'newer.jpg', 'defect_type': 'normal'}]
    assert [item['event_id'] for item in group_inspections(rows)] == ['newer', 'older']
