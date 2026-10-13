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
    """Verify empty history is waiting and never reset ready."""
    result = build_process_snapshot({'mqtt_connected': True}, now=NOW)
    assert result['state'] == 'waiting'
    assert result['reset_ready'] is False
    assert {item['id'] for item in result['equipment']} == {'motor', 'conveyor', 'camera'}
    assert all(item['state'] == 'waiting' for item in result['equipment'])


def test_normal_requires_measured_motion_and_does_not_offer_reset():
    """Verify normal requires measured motion and does not offer reset."""
    result = build_process_snapshot(snapshot(), now=NOW)
    assert result['state'] == 'running'
    assert equipment(result, 'motor')['state'] == 'normal'
    assert equipment(result, 'conveyor')['state'] == 'running'
    assert result['reset_ready'] is False


def test_danger_does_not_fabricate_an_already_stopped_conveyor():
    """Verify danger does not fabricate an already stopped conveyor."""
    data = snapshot(hi=15, level=10)
    result = build_process_snapshot(data, now=NOW)
    assert result['state'] == 'attention'
    assert equipment(result, 'motor')['state'] == 'danger'
    assert equipment(result, 'conveyor')['state'] == 'running'
    assert result['reset_ready'] is False


@pytest.mark.parametrize('running,velocity', [(True, 0), (True, .25), (False, 4), (False, -.2)])
def test_command_and_measured_speed_disagreement_is_a_transition(running, velocity):
    """Verify command and measured speed disagreement is a transition."""
    result = build_process_snapshot(snapshot(running=running, velocity=velocity), now=NOW)
    assert equipment(result, 'conveyor')['state'] == 'warning'
    assert result['state'] == 'attention'


def test_recovered_health_does_not_automatically_restart():
    """Verify recovered health does not automatically restart."""
    result = build_process_snapshot(snapshot(running=False), now=NOW)
    assert result['state'] == 'stopped'
    assert equipment(result, 'conveyor')['state'] == 'stopped'
    assert result['reset_ready'] is True


@pytest.mark.parametrize('key', ['latest_health', 'line'])
@pytest.mark.parametrize('timestamp', [stamp(5.001), stamp(-1), 'bad-date',
                                      '2026-10-10T06:00:00', None])
def test_reset_independently_rejects_bad_health_and_line_times(key, timestamp):
    """Verify reset independently rejects bad health and line times."""
    data = snapshot(running=False)
    data[key]['timestamp'] = timestamp
    result = build_process_snapshot(data, now=NOW)
    assert result['reset_ready'] is False
    assert result['reset_reason']


def test_exact_five_second_observations_satisfy_controller_boundary():
    """Verify exact five second observations satisfy controller boundary."""
    data = snapshot(running=False)
    data['latest_health']['timestamp'] = stamp(5)
    data['line']['timestamp'] = stamp(5)
    result = build_process_snapshot(data, now=NOW)
    assert result['reset_ready'] is True


@pytest.mark.parametrize('hi,level', [(79.99, 0), (95, 1), (80, 10)])
def test_reset_rejects_low_health_or_nonzero_fault(hi, level):
    """Verify reset rejects low health or nonzero fault."""
    assert build_process_snapshot(snapshot(running=False, hi=hi, level=level),
                                  now=NOW)['reset_ready'] is False


def test_reset_health_threshold_is_inclusive():
    """Verify reset health threshold is inclusive."""
    assert build_process_snapshot(snapshot(running=False, hi=80), now=NOW)['reset_ready'] is True


def test_disconnected_mqtt_never_allows_a_reset():
    """Verify disconnected mqtt never allows a reset."""
    data = snapshot(running=False)
    data['mqtt_connected'] = False
    result = build_process_snapshot(data, now=NOW)
    assert result['reset_ready'] is False


@pytest.mark.parametrize('key', ['latest_health', 'line'])
def test_missing_service_observation_never_allows_a_reset(key):
    """Verify missing service observation never allows a reset."""
    data = snapshot(running=False)
    data[key] = None
    assert build_process_snapshot(data, now=NOW)['reset_ready'] is False


def test_old_sensor_cannot_make_motor_look_healthy_with_a_new_health_record():
    """Verify old sensor cannot make motor look healthy with a new health record."""
    data = snapshot()
    data['rows'][0]['timestamp'] = stamp(20)
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'motor')['state'] == 'stale'
    assert equipment(result, 'motor')['fresh'] is False


def test_retained_running_state_cannot_make_a_stale_conveyor_live():
    """Verify retained running state cannot make a stale conveyor live."""
    data = snapshot()
    data['line']['timestamp'] = stamp(60)
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'conveyor')['state'] == 'stale'
    assert result['state'] == 'stale'
    assert result['reset_ready'] is False


@pytest.mark.parametrize('value', [None, 'false', 0])
def test_invalid_running_observation_cannot_claim_a_physical_stop(value):
    """Verify invalid running observation cannot claim a physical stop."""
    data = snapshot(running=False)
    data['line']['running'] = value
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'conveyor')['state'] == 'stale'
    assert result['reset_ready'] is False


@pytest.mark.parametrize('value', [None, math.nan, math.inf])
def test_absent_or_invalid_roller_speed_is_not_a_confirmed_stop(value):
    """Verify absent or invalid roller speed is not a confirmed stop."""
    data = snapshot(running=False)
    data['line']['roller_velocity'] = value
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'conveyor')['state'] == 'stale'
    assert result['reset_ready'] is False


def test_camera_holds_last_inspection_when_the_physical_line_is_stopped():
    """Verify camera holds last inspection when the physical line is stopped."""
    data = snapshot(running=False, hi=15, level=10)
    data['inspections'][0]['timestamp'] = stamp(60)
    result = build_process_snapshot(data, now=NOW)
    camera = equipment(result, 'camera')
    assert camera['state'] == 'stopped'
    assert datetime.fromisoformat(camera['observed_at']) == datetime.fromisoformat(data['inspections'][0]['timestamp'].replace('Z', '+00:00'))
    assert camera['reason']


def test_camera_delays_are_visible_when_the_line_is_running():
    """Verify camera delays are visible when the line is running."""
    data = snapshot()
    data['inspections'][0]['timestamp'] = stamp(60)
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'camera')['state'] == 'stale'


def live_camera(data, *, age=.5, run='run-1'):
    """Attach independently timed frame observations to a live run."""
    data['world'] = {'mqtt_connected': True, 'connected': True, 'stale': False,
                     'state': {'run_id': 'run-1'}, 'cameras': {'inspection': {
                         'timestamp': stamp(age), 'sim_time': 100, 'sequence': 12,
                         'run_id': run, 'age_ms': age * 1000, 'stale': age > 2}}}
    return data


def test_live_camera_health_does_not_expire_between_product_inspections():
    """A ten-second quality cycle must not make a healthy frame feed stale."""
    data = live_camera(snapshot())
    data['inspections'][0]['timestamp'] = stamp(10)
    data['inspections'][0]['run_id'] = 'run-1'
    result = build_process_snapshot(data, now=NOW)
    camera = equipment(result, 'camera')
    assert result['state'] == 'running'
    assert camera['state'] == 'normal' and camera['fresh']
    assert camera['status_label'] == '영상 연결'
    assert camera['metrics']['last_inspection_age_seconds'] == 10
    assert camera['metrics']['last_inspection_current_run'] is True
    assert camera['age_seconds'] == .5


def test_live_camera_status_does_not_claim_its_last_defect_is_a_current_frame_verdict():
    """Keep a defect as quality evidence while reporting frame availability."""
    data = live_camera(snapshot())
    data['inspections'][0].update(defect_type='scratch', bbox=[1, 2, 5, 8])
    result = build_process_snapshot(data, now=NOW)
    camera = equipment(result, 'camera')
    assert camera['state'] == 'normal' and camera['status_label'] == '영상 연결'
    assert camera['metrics']['last_inspection_defective'] is True
    assert any(event['severity'] == 'warning' for event in result['recent_events'])


def test_first_live_frame_does_not_need_a_first_quality_result_to_be_healthy():
    """An uninspected line can still have an observed functioning camera."""
    data = live_camera(snapshot())
    data['inspections'] = []
    camera = equipment(build_process_snapshot(data, now=NOW), 'camera')
    assert camera['fresh'] and camera['state'] == 'normal'
    assert camera['metrics']['last_inspection_at'] is None
    assert camera['metrics']['last_inspection_defective'] is None


@pytest.mark.parametrize('age', [2.001, -1])
def test_live_camera_rejects_stale_or_future_frames_despite_recent_quality(age):
    """A new stored verdict cannot conceal a stale or invalid live feed."""
    camera = equipment(build_process_snapshot(live_camera(snapshot(), age=age), now=NOW), 'camera')
    assert camera['state'] == 'stale' and camera['fresh'] is False


def test_live_camera_rejects_previous_run_and_missing_frame_metadata():
    """Never treat an earlier epoch's video or absent feed as current."""
    data = live_camera(snapshot(), run='run-old')
    assert equipment(build_process_snapshot(data, now=NOW), 'camera')['state'] == 'stale'
    data['world']['cameras'] = {}
    assert equipment(build_process_snapshot(data, now=NOW), 'camera')['state'] == 'waiting'


def test_live_camera_respects_delivery_age_even_if_source_timestamp_is_new():
    """Use the slower source/receipt age so an old cached frame fails closed."""
    data = live_camera(snapshot())
    data['world']['cameras']['inspection']['age_ms'] = 3000
    assert equipment(build_process_snapshot(data, now=NOW), 'camera')['fresh'] is False


def test_live_camera_remains_independent_of_stopped_rollers_and_pose_delay():
    """A stopped line or delayed pose channel does not invent a video outage."""
    data = live_camera(snapshot(running=False))
    data['world'].update(connected=False, stale=True)
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'camera')['fresh']
    assert result['state'] == 'stopped' and result['reset_ready'] is True


def test_live_camera_frame_health_never_relaxes_danger_or_broker_reset_guards():
    """A functioning video feed grants no authority to restart unsafe machinery."""
    data = live_camera(snapshot(running=False, hi=15, level=10))
    assert build_process_snapshot(data, now=NOW)['reset_ready'] is False
    data['mqtt_connected'] = False
    result = build_process_snapshot(data, now=NOW)
    assert equipment(result, 'camera')['fresh'] is False
    assert result['reset_ready'] is False


@pytest.mark.parametrize('value', [None, math.nan, math.inf, -1, 101])
def test_invalid_health_cannot_authorize_reset(value):
    """Verify invalid health cannot authorize reset."""
    data = snapshot(running=False)
    data['latest_health']['health_index'] = value
    assert build_process_snapshot(data, now=NOW)['reset_ready'] is False


def test_datetime_database_values_and_iso_json_values_have_same_freshness():
    """Verify datetime database values and iso json values have same freshness."""
    data = snapshot(running=False)
    expected = build_process_snapshot(data, now=NOW)
    for item in [data['rows'][0], data['latest_health'], data['line'], data['inspections'][0]]:
        item['timestamp'] = NOW-timedelta(seconds=1)
    actual = build_process_snapshot(data, now=NOW)
    assert actual['state'] == expected['state']
    assert actual['reset_ready'] == expected['reset_ready']
    assert [item['fresh'] for item in actual['equipment']] == [item['fresh'] for item in expected['equipment']]


def test_multi_detection_history_is_one_event_without_losing_boxes():
    """Verify multi detection history is one event without losing boxes."""
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
    """Verify grouped history accepts database datetime ordering."""
    rows = [{'id': 'older-0', 'event_id': 'older', 'timestamp': NOW-timedelta(seconds=3),
             'image_path': 'older.jpg', 'defect_type': 'normal'},
            {'id': 'newer-0', 'event_id': 'newer', 'timestamp': NOW-timedelta(seconds=1),
             'image_path': 'newer.jpg', 'defect_type': 'normal'}]
    assert [item['event_id'] for item in group_inspections(rows)] == ['newer', 'older']
