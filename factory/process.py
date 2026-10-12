"""Read-only process state derived from timestamped equipment observations."""
import math
from datetime import datetime, timezone


FRESHNESS_SECONDS = 5
HEALTH_LABELS = {'normal': '정상', 'caution': '주의', 'warning': '경고', 'danger': '위험'}
DEFECT_LABELS = {'normal': '결함 미검출', 'scratch': '스크래치', 'dent': '찍힘', 'contamination': '이물질'}


def _timestamp(value):
    """Accept only timezone-aware observations, never local or malformed dates."""
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def _number(value):
    """Return a finite numeric measurement or an explicitly absent value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _observation(message, now):
    """Compute freshness independently of the HTTP server response timestamp."""
    stamp = _timestamp(message.get('timestamp')) if message else None
    age = (now - stamp).total_seconds() if stamp else None
    return {'observed_at': stamp.isoformat() if stamp else None,
            'age_seconds': round(age, 3) if age is not None else None,
            'fresh': age is not None and 0 <= age <= FRESHNESS_SECONDS}


def group_inspections(rows):
    """Keep every detection under its actual image event without duplicating counts."""
    events = {}
    for row in rows:
        event_id = row['event_id']
        if event_id not in events:
            events[event_id] = {key: row.get(key) for key in
                                ('event_id', 'timestamp', 'image_path', 'gradcam_path',
                                 'health_index_at_time', 'inference_ms')}
            events[event_id].update(detections=[], defective=False, defect_type='normal')
        event = events[event_id]
        if row.get('gradcam_path'):
            event['gradcam_path'] = row['gradcam_path']
        if row.get('defect_type') != 'normal':
            event['detections'].append({key: row.get(key) for key in ('defect_type', 'confidence', 'bbox')})
            event['defective'] = True
            event['defect_type'] = event['detections'][0]['defect_type']
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(events.values(), key=lambda event: (_timestamp(event['timestamp']) or oldest, event['event_id']), reverse=True)


def build_process_snapshot(snapshot, now=None):
    """Describe observed operation; this does not command or infer physical positions."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Process reference time must include a timezone')
    health = snapshot.get('latest_health') or {}
    line = snapshot.get('line') or {}
    rows = snapshot.get('rows') or []
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    sensor = max(rows, key=lambda row: _timestamp(row.get('timestamp')) or oldest) if rows else {}
    images = snapshot.get('inspection_events')
    if images is None:
        images = group_inspections(snapshot.get('inspections') or [])
    image = images[0] if images else {}
    health_time, sensor_time, line_time, image_time = [_observation(item, now) for item in (health, sensor, line, image)]
    mqtt = snapshot.get('mqtt_connected') is True
    hi = _number(health.get('health_index'))
    level = _number(line.get('fault_level'))
    velocity = _number(line.get('roller_velocity'))
    health_valid = hi is not None and 0 <= hi <= 100 and health.get('status') in HEALTH_LABELS
    health_fresh = health_time['fresh'] and health_valid

    motor = {'id': 'motor', 'label': '진단 모터', **health_time,
             'metrics': {'health_index': hi, 'vibration_rms': _number(sensor.get('vibration_x')),
                         'temperature': _number(sensor.get('temperature')), 'rpm': _number(sensor.get('rpm')),
                         'inference_ms': _number(health.get('inference_ms')),
                         'health_observed_at': health_time['observed_at'],
                         'sensor_observed_at': sensor_time['observed_at']}}
    if health_time['age_seconds'] is not None and sensor_time['age_seconds'] is not None:
        motor.update(max((health_time, sensor_time), key=lambda observation: observation['age_seconds']))
    motor['fresh'] = health_fresh and sensor_time['fresh']
    if not health or not sensor:
        motor.update(state='waiting', status_label='센서 대기', reason='센서와 상태 진단 결과 수집 중')
    elif not motor['fresh']:
        motor.update(state='stale', status_label='관측 지연', reason='최근 5초 이내의 유효한 센서·진단 결과가 필요합니다.')
    else:
        motor.update(state=health['status'], status_label=HEALTH_LABELS[health['status']],
                     reason='측정 진동의 FFT·관리도·Autoencoder 진단')

    conveyor = {'id': 'conveyor', 'label': '컨베이어', **line_time,
                'metrics': {'roller_velocity': velocity, 'fault_level': level,
                            'motor_rpm': _number(line.get('motor_rpm')), 'running_requested': line.get('running')}}
    if not line:
        conveyor.update(state='waiting', status_label='라인 대기', reason='Gazebo 라인 관측 수집 중')
    elif not line_time['fresh'] or type(line.get('running')) is not bool or velocity is None or level is None:
        conveyor.update(state='stale', status_label='관측 지연', reason='최근 5초 이내의 유효한 라인 관측이 필요합니다.', fresh=False)
    elif line['running'] and abs(velocity) > .5:
        conveyor.update(state='running', status_label='가동', reason='Gazebo 롤러 회전 관측')
    elif not line['running'] and abs(velocity) <= .01:
        conveyor.update(state='stopped', status_label='정지 · 재시작 대기', reason='롤러 정지 확인 · 진단 모터는 계속 관측')
    else:
        conveyor.update(state='warning', status_label='가동 확인 중' if line['running'] else '정지 중',
                        reason='라인 요청 상태와 실측 롤러 속도를 함께 확인 중')

    camera = {'id': 'camera', 'label': '검사 카메라', **image_time,
              'metrics': {'last_inspection_at': image_time['observed_at'], 'defect_type': image.get('defect_type'),
                          'detections': len(image.get('detections', [])), 'inference_ms': _number(image.get('inference_ms'))}}
    if conveyor['fresh'] and line.get('running') is False:
        camera.update(state='stopped', status_label='검사 일시정지', reason='라인 정지 중 · 마지막 검사 영상 보관')
    elif not image:
        camera.update(state='waiting', status_label='검사 대기', reason='첫 검사 영상 수집 중')
    elif not image_time['fresh']:
        camera.update(state='stale', status_label='검사 지연', reason='마지막 검사 영상 · 최근 검사 갱신 확인 필요')
    elif image['defective']:
        camera.update(state='warning', status_label='결함 검출', reason='현재 검사 영상에서 결함 검출')
    else:
        camera.update(state='normal', status_label='결함 미검출', reason='현재 검사 영상에서 결함이 검출되지 않았습니다.')

    if not mqtt:
        reset_ready, reset_reason = False, 'MQTT 연결 확인 필요'
    elif not health_fresh or not line_time['fresh']:
        reset_ready, reset_reason = False, '최근 5초 이내의 정상 진단·라인 관측 필요'
    elif level != 0:
        reset_ready, reset_reason = False, '적용 고장 레벨을 0으로 낮추세요.'
    elif hi < 80:
        reset_ready, reset_reason = False, 'Health Index 80 이상으로 회복 필요'
    elif conveyor['state'] == 'running':
        reset_ready, reset_reason = False, '현재 가동 중'
    elif conveyor['state'] != 'stopped':
        reset_ready, reset_reason = False, '롤러 정지 관측 확인 필요'
    else:
        reset_ready, reset_reason = True, '정상 진단·레벨 0 확인 · 수동 재시작 가능'

    if not health and not line:
        state, label = 'waiting', '공정 데이터 대기'
    elif not mqtt or motor['state'] == 'stale' or conveyor['state'] == 'stale':
        state, label = 'stale', '연결·관측 확인 필요'
    elif conveyor['state'] == 'stopped':
        state, label = 'stopped', '라인 정지'
    elif motor['state'] in ('caution', 'warning', 'danger') or camera['state'] in ('warning', 'stale') or conveyor['state'] == 'warning':
        state, label = 'attention', '공정 확인 필요'
    elif motor['fresh'] and conveyor['state'] == 'running' and camera['fresh']:
        state, label = 'running', '공정 가동'
    else:
        state, label = 'waiting', '공정 데이터 대기'

    events = [{'timestamp': alarm.get('timestamp'), 'source': alarm.get('source', 'system'),
               'severity': alarm.get('severity', 'warning'), 'message': alarm.get('message', '')}
              for alarm in snapshot.get('alarms', [])]
    for item in images[:6]:
        events.append({'timestamp': item['timestamp'], 'source': 'camera',
                       'severity': 'warning' if item['defective'] else 'normal',
                       'message': '검사 완료 · ' + (' / '.join(dict.fromkeys(DEFECT_LABELS.get(d['defect_type'], d['defect_type'])
                                   for d in item['detections'])) if item['defective'] else '결함 미검출')})
    events.sort(key=lambda event: _timestamp(event['timestamp']) or oldest, reverse=True)
    return {'observed_at': now.isoformat(), 'freshness_seconds': FRESHNESS_SECONDS, 'mqtt_connected': mqtt,
            'state': state, 'label': label, 'reset_ready': reset_ready, 'reset_reason': reset_reason,
            'equipment': [motor, conveyor, camera], 'recent_events': events[:15]}
