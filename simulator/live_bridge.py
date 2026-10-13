"""Forward measured factory poses, real camera frames, and product-linked AI inputs."""
import base64
from collections import deque, OrderedDict
from datetime import datetime, timezone
import json
import logging
import os
import time
import uuid
import cv2
import numpy as np
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String
from factory.common import CONFIG, DATA, connect, publish, utc_now
from factory.layout import get_layout
from simulator.signal import vibration
from simulator.live_evaluation import save_reference, product_roi
from simulator.live_protocol import (ObservationBuffer, AcknowledgedBatch, ResumeLatch,
                                     acknowledged_publish, camera_is_fresh)


def camera_buffer(message):
    """Decode observed RGB/BGR rows while respecting Gazebo's image stride."""
    frame = np.frombuffer(message.data, dtype=np.uint8).reshape(message.height, message.step)
    frame = frame[:, :message.width*3].reshape(message.height, message.width, 3)
    return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) if message.encoding == 'rgb8' else frame.copy()


def encoded_image(frame):
    """Encode an observed camera frame once for separate camera and inspection channels."""
    success, data = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not success:
        raise ValueError('Gazebo camera JPEG encoding failed')
    return base64.b64encode(data).decode('ascii')


def attach_live_topics(node):
    """Add production topics to the baseline acquisition node without altering datasets."""
    node.world = None
    node.world_arrival = -1.
    node.live_client = None
    node.lifecycle_events = deque()
    node.lifecycle_delivery_fault = False
    node.observation_buffer = ObservationBuffer(128)
    node.capture_delivery = OrderedDict()
    node.camera_sent = {'inspection': -1., 'overview': -1.}
    node.camera_latest = {}
    node.defect_reference = None

    def world_message(message):
        """Retain one latest observation and forward it without a replaying network queue."""
        value = json.loads(message.data)
        value['received_at'] = utc_now()
        measured = value.get('observed_wall_time_ms')
        value['timestamp'] = (datetime.fromtimestamp(measured/1000, tz=timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                              if isinstance(measured, (int, float)) else value['received_at'])
        with node.condition:
            node.world, node.world_arrival = value, time.monotonic()
            node.observation_buffer.append(value)
            node.condition.notify_all()
        if node.live_client:
            node.live_client.publish('factory/world/state', json.dumps(value, allow_nan=False), qos=0, retain=True)

    def lifecycle(message):
        """Keep sparse product lifecycle events until the bridge loop can publish them."""
        value = json.loads(message.data)
        if value.get('phase') not in ('entered', 'captured', 'outfeed'):
            return
        value['timestamp'] = utc_now()
        with node.condition:
            if value['phase'] == 'captured':
                value.update(node.capture_delivery.get(value.get('inspection_id'), {}))
            node.lifecycle_events.append(value)
            if len(node.lifecycle_events) >= 256:
                node.lifecycle_delivery_fault = True
                node.conveyor(False)
            node.condition.notify_all()

    def reference(message):
        """Keep simulator truth inside the ROS bridge solely for private held-out labels."""
        value = json.loads(message.data)
        with node.condition:
            node.defect_reference = value
            node.condition.notify_all()

    def camera(message, name):
        """Forward actual camera observations at a bounded independent image rate."""
        now = time.monotonic()
        if now-node.camera_sent[name] < (.50 if name == 'inspection' else .20):
            return
        node.camera_sent[name] = now
        frame = camera_buffer(message)
        payload = {'timestamp': utc_now(), 'sim_time': message.header.stamp.sec+message.header.stamp.nanosec/1e9,
                   'image_base64': encoded_image(frame), 'run_id': node.world.get('run_id') if node.world else None,
                   'camera_id': name, 'world_version': 'live-v1'}
        node.camera_latest[name] = payload
        if node.live_client:
            node.live_client.publish('factory/camera/'+name, json.dumps(payload), qos=0)

    def inspection_camera(message):
        """Stream the real inspection view separately from sparse inference events."""
        camera(message, 'inspection')

    def overview_camera(message):
        """Stream Gazebo's actual factory overview, including a CPU fallback for WebGL."""
        camera(message, 'overview')

    node.create_subscription(String, '/factory/world/state', world_message, 10)
    node.create_subscription(String, '/factory/inspection/event', lifecycle, 10)
    node.create_subscription(String, '/factory/private/defect_reference', reference, 10)
    node.create_subscription(Image, '/factory/inspection/image_raw', inspection_camera, qos_profile_sensor_data)
    node.create_subscription(Image, '/factory/overview/image_raw', overview_camera, qos_profile_sensor_data)


def live_factory(node):
    """Hold inspections until immutable, frame-aligned sensor/image inputs receive PUBACK."""
    state = {'level': CONFIG['simulator']['initial_fault_level'], 'running': True}
    latch = ResumeLatch()
    rng = np.random.default_rng(CONFIG['simulator']['seed'])
    wear_seconds = float(os.environ.get('LIVE_WEAR_SECONDS', '0'))
    if wear_seconds and not 100 <= wear_seconds <= 180:
        raise ValueError('LIVE_WEAR_SECONDS must match calibrated 100–180 second lifetime')
    wear_started = time.monotonic()

    def handler(topic, message):
        """Reject stale retained starts while passing validated stops and exact quality results."""
        if topic == 'factory/command/fault':
            state['level'] = max(0, min(10, int(message['fault_level'])))
            node.publisher.publish(String(data=json.dumps({'fault_level': state['level']})))
        elif topic == 'factory/command/conveyor':
            if type(message.get('running')) is not bool:
                raise ValueError('Conveyor command requires a Boolean running state')
            with node.condition:
                run_id = node.world['run_id'] if node.world else None
                if message['running'] and (run_id is None or not latch.allow_start(message, utc_now(), run_id)):
                    logging.warning('Rejected stale or retired-epoch conveyor start')
                    return
                state['running'] = message['running']
                if state['running']:
                    node.lifecycle_delivery_fault = False
            node.conveyor(state['running'])
        elif topic == 'factory/quality':
            node.publisher.publish(String(data=json.dumps({'quality': message})))

    client = connect('simulator', ['factory/command/fault', 'factory/command/conveyor', 'factory/quality'], handler, max_queue=32)
    node.live_client = client
    layout_batch = AcknowledgedBatch([('factory/world/layout', json.dumps(get_layout(), allow_nan=False), True)])
    seen, recent_captures = set(), deque()
    pending_capture = pending_sensor = None
    connection_seen = False
    next_sensor, last_joint_time = 0., -1.
    startup = time.monotonic()
    directory = DATA/'live'
    directory.mkdir(parents=True, exist_ok=True)

    def local_stop(reason, run_id):
        """Stop through direct ROS even without MQTT and retain an explicit manual-resume latch."""
        with node.condition:
            first = latch.stopped_at is None or latch.run_id != run_id
            latch.stop(utc_now(), run_id)
            state['running'] = False
        if first or node.running:
            node.conveyor(False)
            logging.warning('Physical connection stop: %s; fresh manual restart required', reason)

    def sensor_sample(event_id, joint, world, level, timestamp, inspection=None):
        """Build one immutable synthetic waveform from its actual observed motor state."""
        wave = vibration(level, joint['velocity'][0], joint['position'][0], rng)
        rms = float(np.sqrt(np.mean(wave*wave)))
        sample = {'event_id': event_id, 'timestamp': timestamp, 'sensor_id': 'motor_01',
                  'run_id': world['run_id'], 'world_version': world['world_version'],
                  'vibration_x': rms, 'vibration_y': .8*rms, 'vibration_z': .65*rms,
                  'temperature': 35+2*level+float(rng.normal(0, .3)), 'fault_level': level,
                  'rpm': abs(joint['velocity'][0])*60/(2*np.pi),
                  'sampling_hz': CONFIG['sensor']['sampling_hz'], 'waveform': wave.tolist(),
                  'joint_observation': joint, 'sim_time': joint['sim_time']}
        if inspection:
            sample.update({key: inspection[key] for key in ('product_id', 'inspection_id', 'camera_sim_time', 'camera_timestamp')})
        if wear_seconds:
            age = min(wear_seconds, time.monotonic()-wear_started)
            sample['wear_scenario'] = {'age_seconds': age, 'failure_seconds': wear_seconds,
                                      'true_rul_seconds': wear_seconds-age, 'scope': 'calibrated_synthetic_wear_only'}
        return sample

    def publish_line(level):
        """Publish a freshly observed latest line snapshot, independently of delayed event timestamps."""
        with node.condition:
            world = dict(node.world)
            joint = dict(node.joint)
            reference = node.defect_reference
            connection_stop = latch.stopped_at is not None
        line = {'timestamp': world['timestamp'], 'run_id': world['run_id'],
                'world_version': world['world_version'], 'sim_time': world['sim_time'], 'fault_level': level,
                'running': world['running'], 'inspection_hold': world['inspection_hold'],
                'roller_velocity': joint['velocity'][1], 'motor_rpm': abs(joint['velocity'][0])*60/(2*np.pi),
                'defect_probability': .02+.085*level if reference and reference['defect_mode'] == 'operational' else None,
                'scenario_mode': reference['defect_mode'] if reference else 'waiting',
                'connection_stop_latched': connection_stop,
                'product_count': sum(obj['kind']=='product' for obj in world['objects'])}
        return acknowledged_publish(client, 'factory/line', json.dumps(line, allow_nan=False), retain=True)

    while True:
        now = time.monotonic()
        with node.condition:
            world = dict(node.world) if node.world else None
            joint = dict(node.joint) if node.joint else None
            delivery_fault = node.lifecycle_delivery_fault
        if not world or not joint:
            if now-startup > 45:
                raise TimeoutError('Production Gazebo world/joint observations did not arrive; inspect /tmp/gazebo.log')
            time.sleep(.04)
            continue
        if not client.is_connected():
            if not connection_seen and now-startup < 2.:
                time.sleep(.04)
                continue
            local_stop('MQTT disconnected', world['run_id'])
            time.sleep(.04)
            continue
        connection_seen = True
        if now-node.world_arrival > 2.0 or delivery_fault:
            local_stop('ROS observation stale' if not delivery_fault else 'lifecycle delivery backlog', world['run_id'])
            time.sleep(.04)
            continue
        if not layout_batch.deliver(client):
            local_stop('layout delivery not acknowledged', world['run_id'])
            time.sleep(.04)
            continue
        # Remove an event only after its PUBACK. Failed/reconnecting deliveries
        # retain the same envelope and remain at the head of the queue.
        with node.condition:
            lifecycle = node.lifecycle_events[0] if node.lifecycle_events else None
        if lifecycle:
            if acknowledged_publish(client, 'factory/inspection/event', json.dumps(lifecycle, allow_nan=False)):
                with node.condition:
                    if node.lifecycle_events and node.lifecycle_events[0] is lifecycle:
                        node.lifecycle_events.popleft()
            else:
                local_stop('lifecycle delivery not acknowledged', world['run_id'])
                time.sleep(.04)
                continue
        now = time.monotonic()
        age = min(wear_seconds, now-wear_started) if wear_seconds else None
        level = CONFIG['rul']['failure_level']*age/wear_seconds if wear_seconds else state['level']
        inspection = world.get('inspection')
        if pending_capture:
            if pending_capture['batch'].deliver(client):
                envelope = pending_capture['envelope']
                envelope['delivery_acknowledged_at'] = utc_now()
                envelope['delivery_delay_seconds'] = time.monotonic()-pending_capture['created_monotonic']
                save_reference(pending_capture['image'], envelope, pending_capture['product'],
                               pending_capture['frame_world'], pending_capture['reference'])
                cv2.imwrite(str(directory/'latest.jpg'), pending_capture['image'])
                seen.add(envelope['inspection_id'])
                recent_captures.append(envelope['inspection_id'])
                if len(recent_captures) > 1024:
                    seen.discard(recent_captures.popleft())
                with node.condition:
                    node.capture_delivery[envelope['inspection_id']] = {
                        key: envelope[key] for key in ('event_id', 'sensor_event_id', 'camera_timestamp',
                            'camera_sim_time', 'pose_alignment', 'camera_receipt_age_seconds_at_capture',
                            'delivery_acknowledged_at', 'delivery_delay_seconds')}
                    while len(node.capture_delivery) > 1024:
                        node.capture_delivery.popitem(last=False)
                last_joint_time = pending_capture['sample']['sim_time']
                next_sensor = time.monotonic()+CONFIG['simulator']['publication_interval_s']
                logging.info('Inspection PUBACK complete product=%s inspection=%s delivery_delay=%.3fs',
                             envelope['product_id'], envelope['inspection_id'], envelope['delivery_delay_seconds'])
                pending_capture = None
                publish_line(level)
            else:
                local_stop('sensor/image batch not acknowledged', world['run_id'])
                time.sleep(.04)
                continue
        if inspection and inspection['inspection_id'] in seen and world['running'] and state['running']:
            # A transport stop can race completion. Reissue completion only after
            # a fresh manual start; never create another image or sensor identity.
            node.publisher.publish(String(data=json.dumps({'inspection_complete': inspection['inspection_id']})))
        if not pending_capture and inspection and inspection['phase'] == 'ready' and world['running'] and state['running'] and inspection['inspection_id'] not in seen:
            with node.condition:
                image_time = node.image_time
                arrival = node.image_arrival_time
                image = node.image.copy() if node.image is not None else None
                camera_timestamp = node.image_observed_at
                reference = dict(node.defect_reference) if node.defect_reference else None
                aligned = node.observation_buffer.align(image_time, world['run_id'], inspection['slot_id'],
                                                        inspection['product_id'], inspection['inspection_id'])
                joint = dict(node.joint)
            now = time.monotonic()
            if (aligned and image is not None and camera_is_fresh(now, arrival) and
                    image_time >= inspection['ready_sim_time']+.15 and
                    -.15 <= joint['sim_time']-image_time <= .15 and
                    reference and reference['run_id'] == world['run_id']):
                frame_world, product, alignment = aligned
                reference_ids = {obj['product_id'] for obj in reference['products']}
                frame_ids = {obj['product_id'] for obj in frame_world['objects']}
                if frame_ids == reference_ids:
                    stamp, event_id = utc_now(), str(uuid.uuid4())
                    envelope = {'event_id': event_id, 'sensor_event_id': event_id, 'timestamp': stamp,
                                'run_id': world['run_id'], 'world_version': world['world_version'],
                                'product_id': inspection['product_id'], 'inspection_id': inspection['inspection_id'],
                                'camera_sim_time': image_time, 'camera_timestamp': camera_timestamp, 'sim_time': image_time,
                                'image_base64': encoded_image(image), 'pose_alignment': alignment,
                                'camera_receipt_age_seconds_at_capture': now-arrival,
                                'sensor_sim_time_offset_seconds': joint['sim_time']-image_time,
                                'product_roi': product_roi(product, image.shape[1], image.shape[0]),
                                'image_width': image.shape[1], 'image_height': image.shape[0]}
                    sample = sensor_sample(event_id, joint, world, level, stamp, envelope)
                    batch = AcknowledgedBatch([
                        ('factory/sensor', json.dumps(sample, allow_nan=False), False),
                        ('factory/image', json.dumps(envelope, allow_nan=False), False)])
                    pending_capture = {'batch': batch, 'envelope': envelope, 'sample': sample,
                                       'image': image, 'frame_world': frame_world, 'product': product,
                                       'reference': reference, 'created_monotonic': now}
        if pending_capture is None:
            if pending_sensor is None and now >= next_sensor and joint['sim_time'] > last_joint_time:
                sample = sensor_sample(str(uuid.uuid4()), joint, world, level, utc_now())
                pending_sensor = (sample, AcknowledgedBatch([('factory/sensor', json.dumps(sample, allow_nan=False), False)]))
            if pending_sensor:
                sample, batch = pending_sensor
                if batch.deliver(client):
                    last_joint_time = sample['sim_time']
                    next_sensor = time.monotonic()+CONFIG['simulator']['publication_interval_s']
                    pending_sensor = None
                    if wear_seconds:
                        node.publisher.publish(String(data=json.dumps({'fault_level': level})))
                    publish_line(level)
                else:
                    local_stop('sensor delivery not acknowledged', world['run_id'])
        time.sleep(.04)
