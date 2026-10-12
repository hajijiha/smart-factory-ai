"""Versioned, label-independent operating and rendering scenario schedules."""
import hashlib
import json
import math
import os
import numpy as np

FAULT_TYPES = ('imbalance', 'looseness', 'outer_race', 'inner_race', 'mixed')
VISIBILITY_POLICY_NAME = 'visible_surface_proxy_v1'
RAW_PALETTE_MAX = .65


def apply_visibility_policy(scene, configuration):
    """Declare and map every class into the same high-contrast material domain.

    This material-space gap does not guarantee a rendered pixel difference.
    Raw paired-frame validation remains mandatory. The color scale uses a
    separate label-independent random stream, leaving existing pose/shape/light
    schedules intact. Low-contrast defects are outside this proxy domain.
    """
    settings=configuration.get('data_generation',{})
    name=settings.get('vision_visibility_policy',VISIBILITY_POLICY_NAME)
    gap=float(settings.get('vision_minimum_material_rgb_gap',.30))
    if name!=VISIBILITY_POLICY_NAME or not 0<gap<1:
        raise ValueError('Unsupported vision visibility policy or material RGB gap')
    product=np.asarray(scene['product_color'],dtype=float)
    if product.shape!=(3,) or not np.isfinite(product).all() or (product<0).any() or (product>1).any() or product.min()<=gap+.01:
        raise ValueError('Product material is outside the declared visible-proxy contrast domain')
    index=scene.get('scene_index')
    if index is None:
        index=int(scene['scene_id'].rsplit('-',1)[1])
    color_scale=float(np.random.default_rng(scene['seed']+int(index)*43+88000).uniform(.65,1.))
    upper=float((product.min()-gap)*color_scale)
    parts=[]
    for part in scene['defect_primitives']:
        original=np.asarray(part.get('color_before_contrast',part['color']),dtype=float)
        if original.shape!=(3,) or not np.isfinite(original).all() or (original<0).any() or (original>RAW_PALETTE_MAX).any():
            raise ValueError('Primitive color is outside the declared shared raw palette')
        parts.append({**part,'color_before_contrast':original.tolist(),'color':(original/RAW_PALETTE_MAX*upper).tolist()})
    policy={'name':name,'minimum_material_rgb_gap':gap,'raw_palette_max':RAW_PALETTE_MAX,
        'color_scale':color_scale,'product_minimum_channel':float(product.min()),'primitive_color_upper_bound':upper,
        'scope':'High-contrast colored surface proxies; low-contrast defects and real-factory realism are unvalidated. Material RGB gap does not guarantee rendered pixel completion.'}
    return {**scene,'scene_index':int(index),'visibility_policy':policy,'defect_primitives':parts}


def generator_version():
    value = os.environ.get('DATA_GENERATOR_VERSION', os.environ.get('GENERATOR_VERSION', 'v1'))
    if value not in ('v1', 'v2'):
        raise ValueError(f'Unsupported generator version: {value}')
    return value


def condition_id(parameters):
    payload = json.dumps(parameters, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def groups(modality, configuration):
    """Split complete acquisition families before generating individual samples."""
    settings = configuration.get('data_generation', {})
    count = int(settings.get(f'{modality}_groups', 100))
    if count < 20 or count % 20:
        raise ValueError('Group counts must be positive multiples of 20 (70/15/15 exact)')
    seed = int(settings.get('seed', 20261009)) + (0 if modality == 'sensor' else 10000)
    rng = np.random.default_rng(seed)
    split_counts = settings.get(f'{modality}_split_groups', [count * 70 // 100, count * 15 // 100, count * 15 // 100])
    if len(split_counts) != 3 or sum(split_counts) != count or min(split_counts) < 1:
        raise ValueError('Split group counts must contain three positive counts summing to all groups')
    assignments = [name for name, size in zip(('train', 'val', 'test'), split_counts) for _ in range(int(size))]
    split_offsets = {'train': 0, 'val': int(split_counts[0]), 'test': int(split_counts[0] + split_counts[1])}
    # Preserve balanced type rotations within each split while avoiding capture order by split.
    order = rng.permutation(count)
    for number, position in enumerate(order):
        split = assignments[int(position)]
        group_seed = seed + int(position) * 7919
        prng = np.random.default_rng(group_seed)
        if modality == 'sensor':
            rotation_range = settings.get('sensor_rotation_hz', [8, 15])
            if len(rotation_range) != 2 or not 5 <= rotation_range[0] <= rotation_range[1] <= 50:
                raise ValueError('sensor_rotation_hz must be [low, high] within 5–50 Hz')
            noise_range = settings.get('sensor_noise', [.008, .05])
            if len(noise_range) != 2 or not 0 <= noise_range[0] < noise_range[1]:
                raise ValueError('sensor_noise must contain two increasing nonnegative values')
            cell = (int(position) - split_offsets[split]) % 9
            rpm_bin, noise_bin = divmod(cell, 3)
            rpm_edges = np.linspace(*rotation_range, 4)
            noise_edges = np.linspace(*noise_range, 4)
            parameters = {
                'rotation_hz': float(prng.uniform(rpm_edges[rpm_bin], rpm_edges[rpm_bin+1])),
                'noise': float(prng.uniform(noise_edges[noise_bin], noise_edges[noise_bin+1])),
                'rpm_bin': int(rpm_bin), 'noise_bin': int(noise_bin),
                'colored_noise': float(prng.uniform(0, .018)),
                'gain': float(prng.uniform(.75, 1.25)),
                'load': float(prng.uniform(.7, 1.3)),
                'dc_offset': float(prng.uniform(-.015, .015)),
                'resonance_hz': float(prng.uniform(260, 480)),
                'resonance_decay': float(prng.uniform(16, 35)),
            }
        else:
            material_id = int(position) % 3
            base = float(prng.uniform(.52, .86))
            tint = prng.uniform(-.06, .06, 3)
            parameters = {
                'camera_pose': [float(prng.uniform(-.09, .09)), float(prng.uniform(-.09, .09)),
                    float(prng.uniform(2.65, 2.95)), 0, math.pi / 2 + float(prng.uniform(-.08, .08)), float(prng.uniform(-.09, .09))],
                'product_color': np.clip(base + tint, .35, .95).tolist(),
                'background_color': prng.uniform(.08, .4, 3).tolist(),
                'light': float(prng.uniform(.50, 1.02)),
                'material_id': material_id,
                'specular': [float([.03, .12, .30][material_id])] * 3,
                'image_noise': float(prng.uniform(1, 3)),
            }
        yield {'group_id': f'{modality}-v2-{int(position):04d}', 'group_index': int(position),
               'split': split, 'seed': group_seed, 'condition_id': condition_id(parameters),
               'parameters': parameters}


def sensor_records(group, configuration):
    """Use the same nuisance condition for normal windows and every fault severity."""
    settings = configuration.get('data_generation', {})
    normal = int(settings.get('sensor_normal_per_group', 50))
    repeats = int(settings.get('sensor_fault_per_level_per_group', 2))
    if normal < 1 or repeats < 1:
        raise ValueError('Every sensor group needs normal and all ten fault levels')
    records = [(0, 'normal')] * normal
    for level in range(1, 11):
        for repetition in range(repeats):
            kind = FAULT_TYPES[(group['group_index'] + level + repetition) % len(FAULT_TYPES)]
            records.append((level, kind))
    rng = np.random.default_rng(group['seed'] + 101)
    rng.shuffle(records)
    return records


def defect_primitives(class_id, family_seed, scale=1., darkness=.20):
    """Describe actual Gazebo primitives once, for both rendering and auto-labels.

    The dent remains a surface visual proxy, not a physically deformed CAD mesh.
    Slots have stable names so Gazebo can replace geometry without model spawning.
    """
    rng = np.random.default_rng(family_seed + (class_id + 1) * 1009)
    primitives = []
    def box(size, pose, color):
        primitives.append({'name': f'part{len(primitives)}', 'shape': 'box',
            'size': [float(v) for v in size], 'pose': [float(v) for v in pose], 'color': color})
    def cylinder(radius, length, pose, color):
        primitives.append({'name': f'part{len(primitives)}', 'shape': 'cylinder',
            'radius': float(radius), 'length': float(length), 'pose': [float(v) for v in pose], 'color': color})
    color = [float(darkness)] * 3
    if class_id == 0:
        count = int(rng.integers(1, 6))
        for index in range(count):
            box([rng.uniform(.16, .34) * scale, rng.uniform(.020, .034) * scale, .003],
                [rng.uniform(-.03, .03) * scale, (index - (count - 1) / 2) * .022 * scale, 0, 0, 0, rng.uniform(-.35, .35)], color)
    elif class_id == 1:
        radius = rng.uniform(.065, .105) * scale
        cylinder(radius, .005, [0, 0, 0, 0, 0, 0], [min(darkness + .20, .65)] * 3)
        cylinder(radius * rng.uniform(.52, .78), .007, [0, 0, .003, 0, 0, 0], color)
    elif class_id == 2:
        for _ in range(int(rng.integers(2, 6))):
            # Dark, neutral and brown patches; color alone does not define a class.
            stain = [float(darkness), float(max(.025, darkness * rng.uniform(.60, 1.))), float(max(.025, darkness * rng.uniform(.45, 1.)))]
            cylinder(rng.uniform(.028, .060) * scale, .006,
                [rng.uniform(-.065, .065) * scale, rng.uniform(-.065, .065) * scale, .004, 0, 0, 0], stain)
    elif class_id != -1:
        raise ValueError(f'Unknown defect class: {class_id}')
    return primitives


def vision_scenes(group, configuration):
    settings = configuration.get('data_generation', {})
    normal = int(settings.get('vision_normal_per_group', 30))
    defects = int(settings.get('vision_per_defect_per_group', 10))
    if normal < 1 or defects < 1:
        raise ValueError('Every vision group must contain normal and all defect classes')
    classes = [-1] * normal + [class_id for class_id in range(3) for _ in range(defects)]
    rng = np.random.default_rng(group['seed'] + 202)
    rng.shuffle(classes)
    p = group['parameters']
    for index, class_id in enumerate(classes):
        scale = float(rng.uniform(.68, 1.18))
        darkness = float(rng.uniform(.08, .38))
        camera = list(p['camera_pose'])
        camera[0] += float(rng.uniform(-.015, .015))
        camera[1] += float(rng.uniform(-.015, .015))
        scene = {'generator_version': 'v2', 'group_id': group['group_id'],
            'condition_id': group['condition_id'], 'seed': group['seed'],
            'geometry_family': f"shape-{group['group_index']:04d}",
            'scene_id': f"{group['group_id']}-{index:04d}", 'class_id': class_id,
            'split': group['split'], 'x': float(rng.uniform(-.12, .12)), 'y': float(rng.uniform(-.12, .12)),
            'yaw': float(rng.uniform(-.30, .30)), 'dx': float(rng.uniform(-.15, .15)),
            'dy': float(rng.uniform(-.12, .12)), 'defect_yaw': float(rng.uniform(-1.2, 1.2)),
            'camera_pose': camera, 'product_color': p['product_color'], 'background_color': p['background_color'],
            'material_id': p['material_id'], 'specular': p['specular'], 'image_noise': p['image_noise'],
            'light': float(np.clip(p['light'] + rng.uniform(-.02, .02), .45, 1.07)),
            'defect_scale': scale, 'defect_darkness': darkness,
            'camera_bin': 'low' if camera[2] < 2.8 else 'high',
            'light_bin': 'dim' if p['light'] < .75 else 'bright',
            'background_bin': 'dark' if np.mean(p['background_color']) < .24 else 'light',
            'defect_primitives': defect_primitives(class_id, group['seed'], scale, darkness)}
        yield apply_visibility_policy(scene,configuration)
