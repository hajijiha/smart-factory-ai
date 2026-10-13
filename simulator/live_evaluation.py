"""Private geometric labels for held-out production-world camera evaluation."""
import hashlib
import base64
import json
import math
from pathlib import Path
import cv2
import numpy as np
from scipy.spatial.transform import Rotation
from factory.common import CONFIG, DATA
from simulator.geometry import defect_points
from factory.layout import PRODUCT_SIZE


def product_roi(product, width, height):
    """Project the observed plate's eight corners without using any defect label."""
    half = np.asarray(PRODUCT_SIZE)/2
    corners = np.array([[x, y, z] for x in (-half[0], half[0])
                        for y in (-half[1], half[1]) for z in (-half[2], half[2])])
    world = Rotation.from_quat(product['quaternion']).apply(corners)+np.asarray(product['position'])
    camera_rotation = Rotation.from_euler('xyz', [0, math.pi/2, 0])
    local = camera_rotation.inv().apply(world-[0, 0, 2.8])
    if (local[:, 0] <= .02).any():
        raise ValueError('Observed product crosses the inspection camera near plane')
    focal = .5/math.tan(CONFIG['simulator']['camera_fov']/2)
    uv = np.column_stack([.5-focal*local[:, 1]/local[:, 0], .5-focal*local[:, 2]/local[:, 0]])
    low, high = np.clip(uv.min(axis=0), 0, 1), np.clip(uv.max(axis=0), 0, 1)
    return [float(low[0]*width), float(low[1]*height), float(high[0]*width), float(high[1]*height)]


def reference_geometry(class_id):
    """Recreate exact attached primitives, exclusively for private simulator validation."""
    if class_id == 0:
        parts = [{'shape': 'box', 'size': [.30, .018, .003], 'pose': [0, i*.022-.022, .034, 0, 0, .1]} for i in range(3)]
    elif class_id == 1:
        parts = [{'shape': 'cylinder', 'radius': radius, 'length': .005,
                  'pose': [0, 0, .034+extra, 0, 0, 0]} for radius, extra in [(.095, 0), (.061, .003)]]
    elif class_id == 2:
        parts = [{'shape': 'cylinder', 'radius': .052, 'length': .006,
                  'pose': [x, y, .034, 0, 0, 0]} for x, y in [(-.04, -.02), (.03, .02), (-.01, .06)]]
    else:
        parts = []
    return class_id, parts


def observed_label(product, class_id, allow_partial=False):
    """Project bounded camera-time product pose and known extents into the real camera."""
    class_id, primitives = reference_geometry(class_id)
    if class_id < 0:
        return None, primitives
    points = defect_points(class_id, primitives)
    world = Rotation.from_quat(product['quaternion']).apply(points)+np.asarray(product['position'])
    camera_rotation = Rotation.from_euler('xyz', [0, math.pi/2, 0])
    local = camera_rotation.inv().apply(world-[0, 0, 2.8])
    focal = .5/math.tan(CONFIG['simulator']['camera_fov']/2)
    uv = np.column_stack([.5-focal*local[:, 1]/local[:, 0], .5-focal*local[:, 2]/local[:, 0]])
    if not np.isfinite(uv).all() or (local[:, 0] <= .02).any():
        raise ValueError('Actual defect geometry is outside the inspection image')
    if allow_partial:
        if (uv.max(axis=0) <= 0).any() or (uv.min(axis=0) >= 1).any():
            return None, primitives
        uv = np.clip(uv, 0, 1)
    elif (uv < 0).any() or (uv > 1).any():
        raise ValueError('Actual defect geometry is outside the inspection image')
    low, high = uv.min(axis=0), uv.max(axis=0)
    label = [class_id, *((low+high)/2).tolist(), *(high-low).tolist()]
    return label, primitives


def save_reference(image, envelope, product, world, reference):
    """Save acknowledged frames and bounded approximate geometry labels in private evidence."""
    root = DATA/'factory-v1-evaluation'
    image_dir, label_dir = root/'images'/'test', root/'labels'/'test'
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)
    name = envelope['inspection_id']
    image_path, label_path = image_dir/f'{name}.jpg', label_dir/f'{name}.txt'
    classes = {item['product_id']: item['class_id'] for item in reference['products']}
    if any(item['product_id'] not in classes for item in world['objects'] if item['kind'] == 'product'):
        raise ValueError('Private defect references do not match the current logical products')
    class_id = classes[product['product_id']]
    label, primitives = observed_label(product, class_id)
    target_labels = [] if label is None else [label]
    image_path.write_bytes(base64.b64decode(envelope['image_base64']))
    labels = [] if label is None else [label]
    # The camera may see a neighboring plate edge. Label any visible neighbor
    # geometry as well, so real detections are not wrongly scored as false alarms.
    for neighbor in world['objects']:
        if neighbor['kind'] != 'product' or neighbor['id'] == product['id']:
            continue
        neighbor_label, _ = observed_label(neighbor, classes[neighbor['product_id']], allow_partial=True)
        if neighbor_label is not None:
            labels.append(neighbor_label)
    label_path.write_text(''.join(' '.join(map(str, value))+'\n' for value in labels), encoding='utf-8')
    record = {key: envelope[key] for key in ('event_id', 'product_id', 'inspection_id', 'run_id', 'timestamp', 'camera_sim_time', 'camera_timestamp')}
    record.update(image=str(image_path.relative_to(root)), label=str(label_path.relative_to(root)),
                  ground_truth_class=class_id, defect_primitives=primitives, image_labels=labels,
                  product_roi=envelope['product_roi'], target_product_roi=envelope['product_roi'],
                  target_product_labels=target_labels, image_width=image.shape[1], image_height=image.shape[0],
                  defect_mode=reference['defect_mode'], generated_defect_probability=reference['defect_probability'],
                  product_pose={key: product[key] for key in ('position', 'quaternion')},
                  pose_alignment=envelope.get('pose_alignment'),
                  camera_receipt_age_seconds_at_capture=envelope.get('camera_receipt_age_seconds_at_capture'),
                  sensor_sim_time_offset_seconds=envelope.get('sensor_sim_time_offset_seconds'),
                  delivery_acknowledged_at=envelope.get('delivery_acknowledged_at'),
                  delivery_delay_seconds=envelope.get('delivery_delay_seconds'),
                  pose_sim_time=world['sim_time'], trajectory_group=envelope['product_id'],
                  source='Gazebo contact-driven production world; fixed baseline model held-out evaluation',
                  sha256=hashlib.sha256(image_path.read_bytes()).hexdigest())
    with (root/'manifest.jsonl').open('a', encoding='utf-8') as output:
        output.write(json.dumps(record, allow_nan=False)+'\n')
    (root/'dataset.yaml').write_text(f'path: {root}\ntest: images/test\nval: images/test\nnames: [scratch, dent, contamination]\n', encoding='utf-8')
