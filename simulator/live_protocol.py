"""Bounded frame alignment and acknowledged delivery without ROS dependencies."""
from collections import deque
import copy
from datetime import datetime, timezone
import math


def _slerp(left, right, ratio):
    """Interpolate normalized xyzw rotations along the shortest measured arc."""
    a = [float(value) for value in left]
    b = [float(value) for value in right]
    norm_a, norm_b = math.sqrt(sum(v*v for v in a)), math.sqrt(sum(v*v for v in b))
    if norm_a <= 1e-12 or norm_b <= 1e-12:
        raise ValueError('Measured quaternion has zero length')
    a, b = [v/norm_a for v in a], [v/norm_b for v in b]
    dot = sum(x*y for x, y in zip(a, b))
    if dot < 0:
        b, dot = [-v for v in b], -dot
    dot = min(1., max(-1., dot))
    if dot > .9995:
        result = [x+ratio*(y-x) for x, y in zip(a, b)]
        length = math.sqrt(sum(v*v for v in result))
        return [v/length for v in result]
    angle = math.acos(dot)
    first, second = math.sin((1-ratio)*angle)/math.sin(angle), math.sin(ratio*angle)/math.sin(angle)
    return [first*x+second*y for x, y in zip(a, b)]


class ObservationBuffer:
    """Retain measured worlds and interpolate only between two valid same-run observations."""

    def __init__(self, maximum=128):
        """Bound retained states independently of simulation duration or connection status."""
        self.states = deque(maxlen=maximum)
        self.run_id = None

    def append(self, value):
        """Clear retired epochs and reject duplicate or backwards simulation states."""
        if value['run_id'] != self.run_id:
            self.states.clear()
            self.run_id = value['run_id']
        if self.states and (value['sequence'] <= self.states[-1]['sequence'] or
                            value['sim_time'] <= self.states[-1]['sim_time']):
            return False
        self.states.append(copy.deepcopy(value))
        return True

    def align(self, frame_time, run_id, slot_id, product_id, inspection_id, max_gap=.10):
        """Return an explicitly approximate camera-time pose without extrapolation or slot reuse."""
        if run_id != self.run_id or len(self.states) < 2 or not math.isfinite(frame_time):
            return None
        pair = None
        for left, right in zip(self.states, list(self.states)[1:]):
            if left['sim_time'] <= frame_time <= right['sim_time']:
                pair = left, right
                break
        if pair is None:
            return None
        left, right = pair
        gap = right['sim_time']-left['sim_time']
        if not 0 < gap <= max_gap+1e-9:
            return None
        before = {value['id']: value for value in left['objects']}
        after = {value['id']: value for value in right['objects']}
        target_before, target_after = before.get(slot_id), after.get(slot_id)
        if not target_before or not target_after or any(
                item.get('product_id') != product_id or item.get('inspection_id') != inspection_id
                for item in (target_before, target_after)):
            return None
        ratio = (frame_time-left['sim_time'])/gap
        aligned = copy.deepcopy(right)
        aligned['sim_time'] = frame_time
        objects = []
        for name, first in before.items():
            second = after.get(name)
            if not second or first.get('kind') != 'product':
                continue
            if first.get('product_id') != second.get('product_id'):
                return None  # A replenishment discontinuity needs a newer frame.
            item = copy.deepcopy(second)
            for field in ('position', 'velocity'):
                item[field] = [a+ratio*(b-a) for a, b in zip(first[field], second[field])]
            item['quaternion'] = _slerp(first['quaternion'], second['quaternion'], ratio)
            objects.append(item)
        aligned['objects'] = objects
        metadata = {'method': 'linear_position_and_quaternion_slerp_between_measured_states',
                    'approximate': True, 'extrapolated': False, 'camera_sim_time': frame_time,
                    'before_sim_time': left['sim_time'], 'after_sim_time': right['sim_time'],
                    'before_sequence': left['sequence'], 'after_sequence': right['sequence'],
                    'gap_sim_seconds': gap, 'maximum_gap_sim_seconds': max_gap,
                    'interpolation_ratio': ratio}
        target = next(item for item in objects if item['id'] == slot_id)
        return aligned, target, metadata


def camera_is_fresh(now, received_at, maximum_age=2.):
    """Reject both stale and impossible future camera receipt times at capture creation."""
    return (isinstance(received_at, (int, float)) and math.isfinite(received_at) and
            0 <= now-received_at <= maximum_age)


def acknowledged_publish(client, topic, payload, timeout=2., retain=False):
    """Require accepted enqueue and PUBACK, outside every ROS/network callback thread."""
    if not client.is_connected():
        return False
    try:
        info = client.publish(topic, payload, qos=1, retain=retain)
        if info.rc != 0:
            return False
        info.wait_for_publish(timeout=timeout)
        return info.is_published()
    except (RuntimeError, ValueError, TimeoutError):
        return False


class AcknowledgedBatch:
    """Retry immutable event identities while remembering individually acknowledged parts."""

    def __init__(self, parts):
        """Freeze already-encoded payloads so a partial retry cannot replace a captured frame."""
        self.parts = tuple((topic, payload, retain) for topic, payload, retain in parts)
        self.acknowledged = [False]*len(self.parts)

    def deliver(self, client, timeout=2.):
        """Stop at the first failed PUBACK and retry only remaining unchanged parts later."""
        for index, (topic, payload, retain) in enumerate(self.parts):
            if self.acknowledged[index]:
                continue
            if not acknowledged_publish(client, topic, payload, timeout, retain):
                return False
            self.acknowledged[index] = True
        return True


class ResumeLatch:
    """Require a new, aware operator command after a local physical connection stop."""

    def __init__(self):
        """Track the first stop timestamp until a matching fresh restart is accepted."""
        self.stopped_at = None
        self.run_id = None

    def stop(self, timestamp, run_id):
        """Latch a UTC stop without allowing repeated loop iterations to move its deadline."""
        if self.stopped_at is None or self.run_id != run_id:
            self.stopped_at = timestamp
            self.run_id = run_id

    def allow_start(self, command, now, current_run):
        """Reject retained prior starts, stale commands and commands naming a retired epoch."""
        try:
            stamp = datetime.fromisoformat(command['timestamp'].replace('Z', '+00:00'))
            current = datetime.fromisoformat(now.replace('Z', '+00:00'))
            if stamp.tzinfo is None or current.tzinfo is None:
                return False
            if not 0 <= (current-stamp).total_seconds() <= 5:
                return False
            if command.get('run_id') and command['run_id'] != current_run:
                return False
            if self.stopped_at is not None:
                stopped = datetime.fromisoformat(self.stopped_at.replace('Z', '+00:00'))
                if self.run_id != current_run or stamp <= stopped:
                    return False
            self.stopped_at = None
            self.run_id = current_run
            return True
        except (KeyError, TypeError, ValueError, AttributeError):
            return False
