"""Bounded, ordered Gazebo observations and product evidence for the live web view.

World poses are observed values only. This module never advances a product pose
or interprets missing quality evidence as a normal inspection.
"""
import base64
import copy
import math
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone


WORLD_MAX_AGE = 2.0
CAMERA_MAX_BYTES = 4 * 1024 * 1024
IDENTITY_FIELDS = ('run_id', 'product_id', 'inspection_id', 'camera_timestamp',
                   'camera_sim_time', 'sensor_event_id', 'model_version')


def timestamp(value):
    """Parse only timezone-aware timestamps from the observation source."""
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def finite(value):
    """Reject booleans and nonfinite measurements before sending JSON to browsers."""
    return type(value) in (int, float) and math.isfinite(value)


def vector(value, size):
    """Validate fixed-size numeric pose vectors without guessing missing axes."""
    return isinstance(value, (list, tuple)) and len(value) == size and all(finite(item) for item in value)


class WorldCache:
    """Keep one world/frame per camera and a bounded recent product inventory."""

    def __init__(self, max_products=256, clock=time.monotonic, now=None):
        """Allow deterministic clocks for ordering and freshness regression tests."""
        self.lock = threading.RLock()
        self.clock = clock
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_products = max_products
        self.world = None
        self.received = None
        self.received_at = None
        self.epoch_started_at = None
        self.retired_runs = deque(maxlen=32)
        self.cameras = {}
        self.products = OrderedDict()
        self.health = None
        self.line = None
        self.health_received = None
        self.line_received = None
        self.rejected = 0

    def _age(self, message, received):
        """Require both a recent source clock and a recent accepted local receipt."""
        stamp = timestamp(message.get('timestamp')) if message else None
        if stamp is None or received is None:
            return None
        source = (self.now() - stamp).total_seconds()
        if source < 0:
            return None
        return max(source, self.clock() - received)

    def observe(self, topic, message):
        """Validate and atomically ingest an MQTT observation, never MQTT truth labels."""
        if not isinstance(message, dict):
            return False
        with self.lock:
            if topic == 'factory/world/state':
                accepted = self._world(message)
            elif topic.startswith('factory/camera/'):
                accepted = self._camera(topic.rsplit('/', 1)[-1], message)
            elif topic in ('factory/inspection/event', 'factory/quality', 'factory/gradcam'):
                accepted = self._product(topic, message)
            elif topic in ('factory/health', 'factory/line'):
                if timestamp(message.get('timestamp')) is None:
                    return False
                name = 'health' if topic.endswith('health') else 'line'
                current = getattr(self, name)
                if current and timestamp(message['timestamp']) <= timestamp(current['timestamp']):
                    return False
                setattr(self, name, copy.deepcopy(message))
                setattr(self, name + '_received', self.clock())
                accepted = True
            else:
                return False
            if not accepted:
                self.rejected += 1
            return accepted

    def _world(self, message):
        """Reject duplicate/reordered sequences and resurrected simulator epochs."""
        run = message.get('run_id')
        sequence = message.get('sequence')
        if not isinstance(run, str) or not run or len(run) > 128 or type(sequence) is not int or sequence < 0:
            return False
        if timestamp(message.get('timestamp')) is None or not finite(message.get('sim_time')) or type(message.get('running')) is not bool:
            return False
        if run in self.retired_runs:
            return False
        if self.world:
            if run == self.world['run_id'] and sequence <= self.world['sequence']:
                return False
            if run != self.world['run_id'] and timestamp(message['timestamp']) < timestamp(self.world['timestamp']):
                return False
        objects, joints = message.get('objects'), message.get('joints', [])
        if not isinstance(objects, list) or len(objects) > 512 or not isinstance(joints, list) or len(joints) > 512:
            return False
        clean_objects, clean_joints, identifiers = [], [], set()
        for item in objects:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or item['id'] in identifiers:
                return False
            if not vector(item.get('position'), 3) or not vector(item.get('quaternion'), 4) or not vector(item.get('velocity'), 3):
                return False
            if sum(value * value for value in item['quaternion']) <= 1e-12:
                return False
            identifiers.add(item['id'])
            clean_objects.append({key: copy.deepcopy(item[key]) for key in
                                  ('id', 'kind', 'position', 'quaternion', 'velocity', 'stage', 'inspection_id', 'product_id') if key in item})
        for item in joints:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not finite(item.get('position')) or not finite(item.get('velocity')):
                return False
            clean_joints.append({key: item[key] for key in ('id', 'position', 'velocity')})
        if not self.world or run != self.world['run_id']:
            if self.world:
                self.retired_runs.append(self.world['run_id'])
            self.cameras = {name: frame for name, frame in self.cameras.items() if frame.get('run_id') == run}
            self.epoch_started_at = timestamp(message['timestamp'])
            # Safety observations from the prior process cannot authorize this run.
            self.health = self.line = None
            self.health_received = self.line_received = None
        self.world = {key: copy.deepcopy(message.get(key)) for key in
                      ('run_id', 'sequence', 'timestamp', 'sim_time', 'world_version', 'running')}
        if type(message.get('inspection_hold')) is bool:
            self.world['inspection_hold'] = message['inspection_hold']
        if isinstance(message.get('transport_mode'), str):
            self.world['transport_mode'] = message['transport_mode'][:80]
        if message.get('scenario_mode') in ('operational', 'balanced-evaluation'):
            self.world['scenario_mode'] = message['scenario_mode']
        if type(message.get('lifecycle_reset_count')) is int and message['lifecycle_reset_count'] >= 0:
            self.world['lifecycle_reset_count'] = message['lifecycle_reset_count']
        if isinstance(message.get('inspection'), dict):
            self.world['inspection'] = {key: copy.deepcopy(message['inspection'][key]) for key in
                ('run_id', 'product_id', 'inspection_id', 'model_id', 'event_id', 'captured',
                 'camera_timestamp', 'camera_sim_time', 'stage', 'slot_id', 'phase',
                 'ready_sim_time') if key in message['inspection']}
        self.world.update(objects=clean_objects, joints=clean_joints)
        self.received = self.clock()
        self.received_at = self.now().isoformat().replace('+00:00', 'Z')
        return True

    def _camera(self, name, message):
        """Bound and validate JPEG envelopes, rejecting stale-run and reordered frames."""
        stamp = timestamp(message.get('timestamp'))
        encoded = message.get('image_base64')
        run = message.get('run_id')
        if name not in ('inspection', 'overview') or stamp is None or not finite(message.get('sim_time')):
            return False
        if self.world and run != self.world['run_id']:
            return False
        if not isinstance(encoded, str) or len(encoded) > (CAMERA_MAX_BYTES * 4 // 3 + 8):
            return False
        previous = self.cameras.get(name)
        if previous and stamp <= timestamp(previous['timestamp']):
            return False
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            return False
        if len(data) > CAMERA_MAX_BYTES or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
            return False
        self.cameras[name] = {'timestamp': message['timestamp'], 'sim_time': message['sim_time'],
                              'run_id': run, 'sequence': (previous or {}).get('sequence', 0) + 1,
                              'received': self.clock(), 'bytes': data}
        return True

    def _product(self, topic, message):
        """Reconcile only the same run/product/inspection identity in any arrival order."""
        run, product, inspection = [message.get(key) for key in ('run_id', 'product_id', 'inspection_id')]
        if not all(isinstance(item, str) and 0 < len(item) <= 160 for item in (run, product, inspection)):
            return False
        if timestamp(message.get('timestamp')) is None:
            return False
        key = (run, product, inspection)
        if topic == 'factory/inspection/event' and message.get('phase') not in ('entered', 'captured', 'outfeed'):
            return False
        if topic == 'factory/quality' and (not isinstance(message.get('detections'), list) or not isinstance(message.get('event_id'), str)):
            return False
        existing = self.products.get(key, {})
        evidence_event = existing.get('sensor_event_id') or (existing.get('quality') or {}).get('event_id')
        incoming_event = message.get('sensor_event_id') or message.get('event_id')
        if evidence_event and incoming_event and evidence_event != incoming_event:
            return False
        if incoming_event and any(other_key != key and
                (other.get('sensor_event_id') or (other.get('quality') or {}).get('event_id')) == incoming_event
                for other_key, other in self.products.items()):
            return False
        entry = self.products.setdefault(key, {'run_id': run, 'product_id': product, 'inspection_id': inspection,
                                              'outcome': 'unknown', 'stage': 'inspection_pending'})
        if topic == 'factory/inspection/event':
            phase = message.get('phase')
            if phase not in ('entered', 'captured', 'outfeed'):
                return False
            entry.setdefault(phase + '_at', message['timestamp'])
            # A delayed entered event may enrich history but must not undo outfeed.
            phase_rank = {'entered': 1, 'captured': 2, 'outfeed': 3}
            if phase_rank[phase] >= phase_rank.get(entry.get('phase'), 0):
                entry.update(phase=phase, stage=phase)
            if phase == 'outfeed':
                if vector(message.get('position'), 3):
                    entry['outfeed_position'] = list(message['position'])
                if message.get('outfeed_lane') in ('normal', 'reject', 'unconfirmed', 'unknown'):
                    entry['outfeed_lane'] = message['outfeed_lane']
                if message.get('classification') in ('normal', 'defective', 'unknown'):
                    entry['outfeed_classification'] = message['classification']
        elif topic == 'factory/quality':
            if not isinstance(message.get('detections'), list):
                return False
            existing_event = (entry.get('quality') or {}).get('event_id')
            if existing_event and existing_event != message.get('event_id'):
                return False
            entry['quality'] = {key: copy.deepcopy(message.get(key)) for key in
                                ('event_id', 'timestamp', 'image_path', 'detections', 'defective', 'inference_ms')}
            entry['outcome'] = 'defective' if message['detections'] else 'normal'
        else:
            existing_event = (entry.get('quality') or {}).get('event_id')
            if existing_event and existing_event != message.get('event_id'):
                return False
            entry['gradcam_path'] = message.get('gradcam_path')
        if incoming_event:
            entry['sensor_event_id'] = incoming_event
        for name in IDENTITY_FIELDS:
            if message.get(name) is not None:
                entry[name] = copy.deepcopy(message[name])
        if message.get('health_index_at_time') is not None:
            entry['health_index'] = message['health_index_at_time']
        entry['updated_at'] = max(entry.get('updated_at', message['timestamp']), message['timestamp'])
        self.products.move_to_end(key)
        while len(self.products) > self.max_products:
            self.products.popitem(last=False)
        return True

    def product_snapshot(self, limit=40):
        """Return recent product evidence without mutating the live cache."""
        with self.lock:
            return copy.deepcopy(list(reversed(self.products.values()))[:limit])

    def camera(self, name):
        """Copy an immutable latest-frame envelope for JPEG/MJPEG delivery."""
        with self.lock:
            return self.cameras.get(name, {}).copy() or None

    def snapshot(self, mqtt_connected=False):
        """Describe age and connection explicitly alongside the last observed pose."""
        with self.lock:
            age = self._age(self.world, self.received)
            stale = age is None or age > WORLD_MAX_AGE or not mqtt_connected
            cameras = {}
            for name, frame in self.cameras.items():
                camera_age = self._age(frame, frame['received'])
                cameras[name] = {key: frame[key] for key in ('timestamp', 'sim_time', 'run_id', 'sequence')}
                cameras[name].update(age_ms=round(camera_age * 1000, 1) if camera_age is not None else None,
                                     stale=camera_age is None or camera_age > WORLD_MAX_AGE or not mqtt_connected)
            return {'connected': bool(mqtt_connected and self.world and not stale), 'mqtt_connected': bool(mqtt_connected),
                    'stale': stale, 'age_ms': round(age * 1000, 1) if age is not None else None,
                    'received_at': self.received_at,
                    'state': copy.deepcopy(self.world), 'cameras': cameras, 'rejected_messages': self.rejected}

    def reset_readiness(self):
        """Fail closed unless physical stop and healthy current-run observations agree."""
        with self.lock:
            age = self._age(self.world, self.received)
            if age is None or age > WORLD_MAX_AGE:
                return False, '최근 2초 이내의 Gazebo 위치·관절 관측이 필요합니다.'
            for message, received in ((self.health, self.health_received), (self.line, self.line_received)):
                age = self._age(message, received)
                if age is None or age > 5:
                    return False, '최근 5초 이내의 정상 진단·라인 관측이 필요합니다.'
                if message.get('run_id') and message['run_id'] != self.world['run_id']:
                    return False, '현재 실행의 정상 관측을 기다리세요.'
                if timestamp(message['timestamp']) < self.epoch_started_at:
                    return False, '현재 실행의 정상 관측을 기다리세요.'
            hi = self.health.get('health_index')
            if not finite(hi) or hi < 80 or hi > 100 or self.health.get('status') != 'normal' or self.line.get('fault_level') != 0:
                return False, '고장 레벨 0과 Health Index 80 이상이 필요합니다.'
            velocity = self.line.get('roller_velocity')
            if self.world.get('running') or self.line.get('running') is not False or not finite(velocity) or abs(velocity) > .01:
                return False, '실제 롤러 정지 관측을 기다리세요.'
            if any(abs(item['velocity']) > .01 for item in self.world['joints'] if item['id'].startswith('roller')):
                return False, '실제 롤러 정지 관측을 기다리세요.'
            return True, '정상 회복·물리 정지 확인'
