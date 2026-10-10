"""Independent dataset audit: integrity, split groups, coverage and nuisance shortcuts.

Run with ``python -m scripts.bias_audit --data /path/data --output report.json``.
This command reads data and source; it does not generate samples or fit task models.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np

from scripts.bias_metrics import (PROTOCOL, assert_group_separation, load_protocol,
                                  iou, nuisance_cv, nuisance_difference, sha256, write_report)


def issue(report, severity, code, detail):
    """Keep failures, warnings and unvalidated claims separate in the final report."""
    report['findings'].append({'severity': severity, 'code': code, 'detail': detail})


def verify_versioned_marker(root, expected_count, report, modality, protocol=None):
    """Recompute marker fingerprint/file/source hashes rather than trusting version claims."""
    marker = json.loads((root / 'COMPLETE').read_text())
    required = ['schema_version', 'generator_version', 'settings', 'source_hashes', 'fingerprint', 'files', 'row_count', 'plugin_binding']
    if not all(key in marker for key in required):
        issue(report, 'FAIL', modality + '_incomplete_marker', required)
        return marker
    canonical = {key: marker[key] for key in ['schema_version', 'generator_version', 'settings', 'source_hashes', 'plugin_binding']}
    actual = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if marker['fingerprint'] != actual or marker['schema_version'] != 2 or marker['generator_version'] != 'v2':
        issue(report, 'FAIL', modality + '_marker_fingerprint', 'Marker specification hash/schema is invalid')
    if marker['row_count'] != expected_count:
        issue(report, 'FAIL', modality + '_marker_count', {'declared': marker['row_count'], 'actual': expected_count})
    for name, digest in marker['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or sha256(path) != digest:
            issue(report, 'FAIL', modality + '_marker_file_hash', name)
    source_root = Path(__file__).resolve().parents[1]
    binding = marker['plugin_binding']
    import re
    binding_valid = (isinstance(binding, dict) and binding.get('schema_version') == 1
                     and binding.get('source_path') == 'simulator/plugin/world.cpp'
                     and binding.get('library_path') == 'simulator/libfactory_world.so'
                     and re.fullmatch('[0-9a-f]{64}', str(binding.get('source_sha256', ''))) is not None
                     and re.fullmatch('[0-9a-f]{64}', str(binding.get('library_sha256', ''))) is not None)
    if not binding_valid:
        issue(report, 'FAIL', modality + '_plugin_binding_schema', 'Missing/invalid source-to-binary binding')
    elif sha256(source_root / binding['source_path']) != binding['source_sha256']:
        issue(report, 'FAIL', modality + '_stale_plugin_source_binding', binding['source_path'])
    elif marker['source_hashes'].get(binding['source_path']) != binding['source_sha256']:
        issue(report, 'FAIL', modality + '_plugin_source_identity_alignment', 'Plugin source differs from the dataset source identity')
    report[modality + '_plugin_verification_scope'] = {
        'source': 'Current source and recorded source hash are checked independently',
        'library': 'Recorded binary hash/schema checked; binary need not be present in audit container',
        'loaded_library': 'Producer verifies the loaded gzserver path/inode at acquisition; audit does not repeat a live process check'}
    required_sources = {'simulator/scenarios.py', 'simulator/signal.py', 'simulator/world.py',
                        'simulator/geometry.py', 'factory/pdm/features.py', 'simulator/plugin/world.cpp',
                        'simulator/Dockerfile', 'simulator/start.sh', 'simulator/dataset_protocol.py',
                        'simulator/sensor_dataset.py', 'simulator/vision_dataset.py', 'simulator/bridge.py',
                        'simulator/preflight.py', 'simulator/render_validation.py'}
    if not required_sources.issubset(marker['source_hashes']):
        issue(report, 'FAIL', modality + '_incomplete_source_identity', sorted(required_sources - set(marker['source_hashes'])))
    for name, digest in marker['source_hashes'].items():
        source = (source_root / name).resolve()
        if not source.is_relative_to(source_root) or not source.is_file() or sha256(source) != digest:
            issue(report, 'FAIL', modality + '_stale_generator_source', name)
    # Independently compare the generation-related config subset, without importing
    # the producer's fingerprint/cache helper or any MQTT client.
    import os
    import yaml
    configuration = yaml.safe_load(Path(os.environ.get('FACTORY_CONFIG', str(source_root / 'config.yaml'))).read_text())
    settings = {key: configuration[key] for key in ['sensor', 'data_generation'] if key in configuration}
    settings['camera'] = {key: configuration['simulator'][key] for key in ['camera_fov', 'camera_size']}
    if settings != marker['settings']:
        issue(report, 'FAIL', modality + '_stale_generator_config', 'Current generation settings differ from dataset settings')
    report[modality + '_completion'] = marker
    if modality == 'vision':
        verify_render_gate(root, marker, report, protocol or load_protocol()[0])
    return marker


def independently_compare_pixels(before, after, expected, criteria):
    """Recompute the paired-pixel gate without importing the producer's comparison."""
    if before is None or after is None or before.shape != after.shape:
        raise ValueError('Rendering pair is unreadable or differently sized')
    differences = np.abs(after.astype(np.int16) - before.astype(np.int16))
    changed = np.max(differences, axis=2) > criteria['changed_pixel_channel_threshold']
    y, x = np.nonzero(changed)
    count = len(x)
    if not count:
        return {'status': 'FAIL', 'changed_pixels': 0, 'outside_roi_pixels': 0, 'bbox_iou': 0., 'raw_bbox_iou': 0.,
                'outside_roi_fraction': 1., 'observed_bbox_pixels': None}
    observed = np.asarray([min(x), min(y), max(x) + 1, max(y) + 1], float)
    expected = np.asarray(expected, float)
    if expected.shape != (4,) or not np.isfinite(expected).all() or expected[2] <= expected[0] or expected[3] <= expected[1]:
        raise ValueError('Invalid expected rendering box')
    tolerance = criteria['rounding_tolerance_pixels']
    corrected = observed.copy()
    for edge in range(4):
        if abs(observed[edge] - expected[edge]) <= tolerance:
            corrected[edge] = expected[edge]
    within = ((x >= expected[0] - tolerance) & (x < expected[2] + tolerance)
              & (y >= expected[1] - tolerance) & (y < expected[3] + tolerance))
    outside = float(np.mean(~within))
    overlap, raw_overlap = iou(expected, corrected), iou(expected, observed)
    passed = (count >= criteria['minimum_changed_pixels'] and overlap >= criteria['minimum_bbox_iou']
              and outside <= criteria['maximum_outside_roi_fraction'])
    return {'status': 'PASS' if passed else 'FAIL', 'changed_pixels': count, 'outside_roi_pixels': int(np.sum(~within)), 'bbox_iou': overlap,
            'raw_bbox_iou': raw_overlap, 'outside_roi_fraction': outside, 'observed_bbox_pixels': observed.tolist()}


CAPTURE_POLICY = {'minimum_wall_seconds_after_ack': .25, 'minimum_fresh_frames': 3,
                  'minimum_sim_seconds_after_ack': .10}
SCENE_APPLICATION_POLICY = {'application_rounds': 3, 'minimum_seconds_between_ack_and_next_command': .10}


def verify_scene_application(sync, scene_id, report, record_id):
    """Verify the fixed full-state send schedule, without claiming rendering completion."""
    records = sync.get('scene_applications', [])
    if sync.get('scene_application_policy') != SCENE_APPLICATION_POLICY or len(records) != 3:
        issue(report, 'FAIL', 'vision_scene_application_policy', record_id)
        return False
    required = ['scene_id', 'scene_application_id', 'scene_command_id', 'scene_apply_round',
                'command_monotonic', 'ack_arrival_monotonic', 'ack_sim_time', 'ack_image_sequence', 'ack_scope']
    valid = all(isinstance(record, dict) and all(key in record for key in required) for record in records)
    if valid:
        transaction = records[0]['scene_application_id']
        valid = isinstance(transaction, str) and bool(transaction) and len({record['scene_command_id'] for record in records}) == 3
        for index, record in enumerate(records):
            valid = (valid and record['scene_id'] == scene_id and record['scene_application_id'] == transaction
                     and record['scene_apply_round'] == index and record['scene_command_id'] == f'{transaction}:{index}'
                     and record['ack_scope'] == 'physics_applied_and_visual_messages_enqueued'
                     and all(isinstance(record[key], (int, float)) and np.isfinite(record[key]) for key in
                             ['command_monotonic', 'ack_arrival_monotonic', 'ack_sim_time', 'ack_image_sequence']))
            if valid:
                valid = record['ack_arrival_monotonic'] >= record['command_monotonic'] and record['ack_image_sequence'] >= 0
                if index:
                    previous = records[index - 1]
                    valid = (valid and record['command_monotonic'] >= previous['ack_arrival_monotonic'] + .10 - 1e-12
                             and record['ack_sim_time'] >= previous['ack_sim_time']
                             and record['ack_image_sequence'] >= previous['ack_image_sequence'])
        if valid:
            final = records[-1]
            valid = (sync['ack_arrival_monotonic'] == final['ack_arrival_monotonic']
                     and sync['ack_sim_time'] == final['ack_sim_time'] and sync['ack_image_sequence'] == final['ack_image_sequence'])
    if not valid:
        issue(report, 'FAIL', 'vision_scene_application_sequence', record_id)
    return bool(valid)


def verify_capture_sync(sync, scene_id, camera_sim_time, joint_sim_time, report, record_id):
    """Check recorded transport/frame barriers; timing alone is not pixel-scene proof."""
    required = ['scene_id', 'ack_scene_id', 'ack_image_sequence', 'ack_arrival_monotonic', 'ack_sim_time',
                'first_fresh_sequence', 'image_sequence', 'fresh_frames', 'image_arrival_monotonic',
                'capture_monotonic', 'settling_wall_seconds', 'camera_sim_time', 'joint_sim_time', 'policy',
                'fresh_frame_sequences', 'fresh_frame_sim_times']
    if not isinstance(sync, dict) or any(key not in sync for key in required):
        issue(report, 'FAIL', 'vision_capture_sync_missing', record_id)
        return False
    valid = sync['policy'] == CAPTURE_POLICY and sync['scene_id'] == scene_id and sync['ack_scene_id'] == scene_id
    numeric_keys = [key for key in required if key not in ['scene_id', 'ack_scene_id', 'policy',
                                                        'fresh_frame_sequences', 'fresh_frame_sim_times']]
    valid = valid and all(isinstance(sync[key], (int, float)) and np.isfinite(sync[key]) for key in numeric_keys)
    if valid:
        ack_sequence, first, last = sync['ack_image_sequence'], sync['first_fresh_sequence'], sync['image_sequence']
        valid = (all(isinstance(sync[key], int) and not isinstance(sync[key], bool) for key in
                     ['ack_image_sequence', 'first_fresh_sequence', 'image_sequence', 'fresh_frames'])
                 and ack_sequence >= 0 and first > ack_sequence and last >= first
                 and sync['fresh_frames'] >= 3)
        sequences, sim_times = sync['fresh_frame_sequences'], sync['fresh_frame_sim_times']
        valid = (valid and isinstance(sequences, list) and isinstance(sim_times, list)
                 and len(sequences) == len(sim_times) == sync['fresh_frames']
                 and all(isinstance(value, int) and not isinstance(value, bool) for value in sequences)
                 and all(isinstance(value, (int, float)) and np.isfinite(value) for value in sim_times))
        if valid:
            valid = (sequences[0] == first and sequences[-1] == last and np.all(np.diff(sequences) > 0)
                     and np.all(np.diff(sim_times) > 0) and min(sim_times) >= sync['ack_sim_time'] + .10 - 1e-12
                     and np.isclose(sim_times[-1], sync['camera_sim_time'], rtol=0, atol=1e-12))
        elapsed = sync['image_arrival_monotonic'] - sync['ack_arrival_monotonic']
        valid = (valid and elapsed >= .25 - 1e-12 and sync['capture_monotonic'] >= sync['image_arrival_monotonic']
                 and np.isclose(sync['settling_wall_seconds'], elapsed, rtol=0, atol=1e-12)
                 and sync['camera_sim_time'] >= sync['ack_sim_time'] + .10 - 1e-12
                 and sync['joint_sim_time'] >= sync['ack_sim_time'] + .10 - 1e-12
                 and np.isclose(sync['camera_sim_time'], camera_sim_time, rtol=0, atol=1e-12)
                 and np.isclose(sync['joint_sim_time'], joint_sim_time, rtol=0, atol=1e-12))
    if not valid:
        issue(report, 'FAIL', 'vision_capture_sync_inconsistent', record_id)
    application_valid = verify_scene_application(sync, scene_id, report, record_id)
    return bool(valid and application_valid)


def rendering_criteria(protocol):
    gate = protocol['render_gate']
    return {key: gate[key] for key in ['minimum_bbox_iou', 'maximum_outside_roi_fraction',
            'rounding_tolerance_pixels', 'changed_pixel_channel_threshold', 'minimum_changed_pixels']}


def verify_visibility_policy(scene, settings, report, record_id):
    """Check a declared synthetic visibility domain, rather than infer it from PASS pixels."""
    expected_name = settings.get('vision_visibility_policy')
    if expected_name is None:
        return True
    policy = scene.get('visibility_policy')
    if expected_name != 'visible_surface_proxy_v1' or not isinstance(policy, dict):
        issue(report, 'FAIL', 'vision_visibility_policy_missing', record_id)
        return False
    required = ['name', 'minimum_material_rgb_gap', 'raw_palette_max', 'color_scale',
                'product_minimum_channel', 'primitive_color_upper_bound', 'scope']
    if any(key not in policy for key in required):
        issue(report, 'FAIL', 'vision_visibility_policy_schema', record_id)
        return False
    product = np.asarray(scene['product_color'], float)
    numeric = [policy[key] for key in required if key not in ['name', 'scope']]
    valid = (product.shape == (3,) and np.isfinite(product).all()
             and all(isinstance(scene.get(key), int) and not isinstance(scene.get(key), bool) for key in ['seed', 'scene_index'])
             and scene['scene_index'] >= 0
             and all(isinstance(value, (int, float)) and not isinstance(value, bool) and np.isfinite(value) for value in numeric)
             and policy['name'] == expected_name and isinstance(policy['scope'], str) and bool(policy['scope']))
    if valid:
        gap, scale, palette = policy['minimum_material_rgb_gap'], policy['color_scale'], policy['raw_palette_max']
        bound = (float(min(product)) - gap) * scale
        expected_scale = float(np.random.default_rng(scene['seed'] + scene['scene_index'] * 43 + 88000).uniform(.65, 1))
        valid = (gap == settings.get('vision_minimum_material_rgb_gap') and 0 < gap < min(product)
                 and .65 <= scale <= 1 and palette == .65
                 and np.isclose(scale, expected_scale, rtol=0, atol=1e-12)
                 and np.isclose(policy['product_minimum_channel'], min(product), rtol=0, atol=1e-12)
                 and np.isclose(policy['primitive_color_upper_bound'], bound, rtol=0, atol=1e-12))
        for primitive in scene.get('defect_primitives', []):
            original = np.asarray(primitive.get('color_before_contrast', []), float)
            mapped = np.asarray(primitive.get('color', []), float)
            valid = (valid and original.shape == mapped.shape == (3,) and np.isfinite(original).all()
                     and np.isfinite(mapped).all() and np.all((original >= 0) & (original <= palette)))
            if valid:
                valid = (np.allclose(mapped, original / palette * bound, rtol=0, atol=1e-12)
                         and np.all(product - mapped >= gap - 1e-12))
    if not valid:
        issue(report, 'FAIL', 'vision_visibility_policy_consistency', record_id)
    return bool(valid)


def verify_render_pair(root, case, fingerprint, report, criteria, directory):
    """Recompute one actual raw PNG pair, independently of producer PASS text."""
    import cv2
    root = Path(root).resolve()
    pair_id = case.get('pair_id', '<missing>')
    if (case.get('fingerprint') != fingerprint or case.get('model_used') is not False
            or case.get('image_noise_applied') is not False or case.get('criteria') != criteria):
        issue(report, 'FAIL', 'vision_render_pair_identity', pair_id)
    images = {}
    for kind in ['normal', 'defect']:
        path = (root / case[kind + '_image']).resolve()
        if (not path.is_relative_to(root / directory) or path.suffix.lower() != '.png'
                or not path.is_file() or sha256(path) != case[kind + '_sha256']):
            issue(report, 'FAIL', 'vision_render_pair_hash', pair_id + '/' + kind)
            continue
        images[kind] = cv2.imread(str(path))
    if len(images) != 2:
        return None
    normal, defect = case['normal_scene'], case['defect_scene']
    generation_settings = report.get('vision_completion', {}).get('settings', {}).get('data_generation', {})
    for kind, scene in [('normal', normal), ('defect', defect)]:
        verify_visibility_policy(scene, generation_settings, report, pair_id + '/' + kind)
    same_inputs = ['x', 'y', 'yaw', 'dx', 'dy', 'defect_yaw', 'camera_pose', 'product_color',
                   'background_color', 'material_id', 'specular', 'light', 'seed', 'group_id', 'condition_id']
    if (normal.get('class_id') != -1 or defect.get('class_id') != case['class_id']
            or case['class_id'] not in [0, 1, 2] or normal.get('scene_id') == defect.get('scene_id')
            or defect.get('defect_scale') != case['scale'] or normal.get('defect_primitives') != []
            or any(key not in normal or key not in defect or normal[key] != defect[key] for key in same_inputs)):
        issue(report, 'FAIL', 'vision_render_pair_control', pair_id)
    if generation_settings.get('vision_visibility_policy') and normal.get('visibility_policy') != defect.get('visibility_policy'):
        issue(report, 'FAIL', 'vision_render_pair_visibility_control', pair_id)
    sync_valid = []
    for kind, scene in [('normal', normal), ('defect', defect)]:
        sync_valid.append(verify_capture_sync(case.get(kind + '_capture_sync'), scene['scene_id'],
            case[kind + '_camera_sim_time'], case[kind + '_joint']['sim_time'], report, pair_id + '/' + kind))
    if all(sync_valid) and case['normal_capture_sync']['image_sequence'] >= case['defect_capture_sync']['image_sequence']:
        issue(report, 'FAIL', 'vision_render_pair_frame_order', pair_id)
    fresh = independently_compare_pixels(images['normal'], images['defect'], case['expected_bbox_pixels'], criteria)
    recorded = case['metrics']
    consistent = all(fresh.get(key) == recorded.get(key) if key in ['status', 'changed_pixels', 'outside_roi_pixels', 'observed_bbox_pixels']
                     else np.isclose(fresh[key], recorded.get(key, -1), rtol=0, atol=1e-12) for key in fresh)
    consistent = (consistent and case.get('gt_bbox_xyxy') == case['expected_bbox_pixels']
                  and case.get('difference_bbox_xyxy') == fresh['observed_bbox_pixels'])
    if fresh['status'] != 'PASS' or not consistent:
        issue(report, 'FAIL', 'vision_render_pixel_consistency', {'pair_id': pair_id, 'recomputed': fresh})
    return {'pair_id': pair_id, 'class_id': case['class_id'], 'scale': case['scale'],
            'image_shape': list(images['defect'].shape), **fresh}


def verify_render_gate(root, marker, report, protocol):
    """Bind nine actual-image comparisons to the dataset and recompute every metric."""
    root = Path(root)
    binding = marker.get('render_check', {})
    expected_binding = {'path': 'render-check.json', 'schema_version': 1, 'status': 'PASS',
                        'case_count': 9, 'model_used': False}
    if binding != expected_binding or binding.get('model_used') is not False:
        issue(report, 'FAIL', 'vision_render_gate_binding', 'COMPLETE must bind a model-free PASS for all nine paired cases')
        return
    evidence_path = root / binding['path']
    if marker.get('files', {}).get(binding['path']) != sha256(evidence_path):
        issue(report, 'FAIL', 'vision_render_gate_hash', 'Render check bytes do not match COMPLETE')
        return
    evidence = json.loads(evidence_path.read_text())
    gate = protocol['render_gate']
    criteria = rendering_criteria(protocol)
    for key, expected in [('schema_version', 1), ('generator_schema_version', 2), ('generator_version', 'v2'),
                          ('status', 'PASS'), ('model_used', False), ('case_count', 9), ('criteria', criteria),
                          ('fingerprint', marker['fingerprint']), ('source_hashes', marker['source_hashes']),
                          ('settings', marker['settings']), ('plugin_binding', marker['plugin_binding']),
                          ('capture_policy', CAPTURE_POLICY), ('scene_application_policy', SCENE_APPLICATION_POLICY)]:
        if evidence.get(key) != expected or key == 'model_used' and evidence.get(key) is not False:
            issue(report, 'FAIL', 'vision_render_gate_metadata', key)
    cases = evidence.get('cases', [])
    matrix = {(int(case['class_id']), float(case['scale'])) for case in cases}
    expected_matrix = {(cls, scale) for cls in gate['classes'] for scale in gate['scales']}
    if len(cases) != 9 or matrix != expected_matrix or len({case['pair_id'] for case in cases}) != 9:
        issue(report, 'FAIL', 'vision_render_gate_matrix', 'Expected three classes by three scales, each once')
        return
    recomputed = []
    for case in cases:
        fresh = verify_render_pair(root, case, marker['fingerprint'], report, criteria, 'render-check')
        if fresh is not None:
            recomputed.append(fresh)
    report['vision_render_gate'] = {'file_sha256': sha256(evidence_path), 'cases_recomputed': len(recomputed),
                                  'criteria': criteria, 'cases': recomputed, 'model_used': False,
                                  'capture_policy': CAPTURE_POLICY,
                                  'scene_application_policy': SCENE_APPLICATION_POLICY,
                                  'method': 'Independent raw PNG pixel comparison, with explicit per-edge rounding tolerance'}


def verify_bulk_render_pairs(root, records, marker, report, protocol):
    """Check every positive training/validation/test row; no sampled-only pixel audit."""
    criteria = rendering_criteria(protocol)
    positives = [row for row in records if row['scene']['class_id'] >= 0]
    expected = {'status': 'PASS', 'pair_count': len(positives), 'method': 'all_defect_normal_pairs',
                'model_used': False, 'image_noise_applied': False, 'criteria': criteria}
    binding = marker.get('bulk_render_check', {})
    if (binding != expected or binding.get('model_used') is not False or binding.get('image_noise_applied') is not False
            or marker.get('render_pair_count') != len(positives)):
        issue(report, 'FAIL', 'vision_bulk_render_binding', {'expected': expected, 'actual': binding})
    pair_ids, image_paths, recomputed, counts = set(), set(), [], Counter()
    generation_settings = marker.get('settings', {}).get('data_generation', {})
    for row in records:
        verify_visibility_policy(row['scene'], generation_settings, report, row['image'])
        case = row.get('render_pair')
        if row['scene']['class_id'] < 0:
            if case is not None:
                issue(report, 'FAIL', 'vision_normal_row_has_render_pair', row['image'])
            continue
        if not isinstance(case, dict):
            issue(report, 'FAIL', 'vision_defect_render_pair_missing', row['image'])
            continue
        if (case['defect_scene'] != row['scene'] or case['defect_capture_sync'] != row.get('capture_sync')
                or case['defect_joint'] != row['joint'] or case['defect_camera_sim_time'] != row['camera_sim_time']
                or case['pair_id'] != row['scene']['scene_id'] or row.get('fingerprint') != marker['fingerprint']
                or row.get('generator_version') != 'v2'):
            issue(report, 'FAIL', 'vision_bulk_pair_manifest_alignment', row['image'])
        if (case['pair_id'] in pair_ids or case['normal_image'] in image_paths or case['defect_image'] in image_paths
                or case['normal_image'] == case['defect_image']):
            issue(report, 'FAIL', 'vision_bulk_pair_reused', case['pair_id'])
        pair_ids.add(case['pair_id']); image_paths.update([case['normal_image'], case['defect_image']])
        fresh = verify_render_pair(root, case, marker['fingerprint'], report, criteria, 'render-pairs')
        if fresh is None:
            continue
        height, width = fresh['image_shape'][:2]
        label_path = Path(root) / 'labels' / row['scene']['split'] / (Path(row['image']).stem + '.txt')
        label = label_path.read_text().split()
        label_valid = len(label) == 5 and int(label[0]) == case['class_id']
        if label_valid:
            cx, cy, box_width, box_height = map(float, label[1:])
            label_box = [(cx - box_width / 2) * width, (cy - box_height / 2) * height,
                         (cx + box_width / 2) * width, (cy + box_height / 2) * height]
            label_valid = np.allclose(label_box, case['expected_bbox_pixels'], rtol=0, atol=1e-6)
        if not label_valid:
            issue(report, 'FAIL', 'vision_bulk_pair_label_alignment', row['image'])
        recomputed.append({'image': row['image'], 'split': row['scene']['split'], **fresh})
        counts[row['scene']['split'], int(case['class_id'])] += 1
    if len(recomputed) != len(positives):
        issue(report, 'FAIL', 'vision_bulk_pair_coverage', {'positive_rows': len(positives), 'recomputed_pairs': len(recomputed)})
    report['vision_bulk_render_gate'] = {'positive_rows': len(positives), 'cases_recomputed': len(recomputed),
        'criteria': criteria, 'model_used': False, 'image_noise_applied': False,
        'capture_policy': CAPTURE_POLICY, 'scene_application_policy': SCENE_APPLICATION_POLICY,
        'counts': {f'{split}/{cls}': count for (split, cls), count in sorted(counts.items())},
        'cases': recomputed, 'method': 'Every defect row: independent raw PNG pixels plus manifest and learning-label alignment'}


def numeric_nuisance(row):
    """Read sensor nuisance inputs, excluding fault level/type and outcome features."""
    params = row.get('signal_params', {})
    omega = row.get('joint', {}).get('velocity', [0])[0]
    values = {'rotation_hz': abs(float(omega)) / (2 * np.pi)}
    for key in ['noise', 'noise_std', 'gain', 'load', 'dc_offset', 'colored_noise']:
        value = params.get(key, row.get(key))
        if isinstance(value, (int, float)):
            values[key] = float(value)
    return values


def audit_sensor(root, report, protocol):
    """Validate aligned records and evaluate nuisance/class associations."""
    path = root / 'sensor/dataset.npz'
    if not path.exists():
        issue(report, 'FAIL', 'sensor_missing', str(path))
        return
    rows = [json.loads(line) for line in (root / 'sensor/provenance.jsonl').read_text().splitlines() if line]
    with np.load(path, allow_pickle=False) as data:
        x, levels, splits = data['features'], data['levels'], data['splits']
        if x.shape != (len(rows), 9) or not np.isfinite(x).all():
            issue(report, 'FAIL', 'sensor_shape_or_finite', {'shape': list(x.shape), 'records': len(rows)})
            return
        if not np.array_equal(levels, [row['level'] for row in rows]) or not np.array_equal(splits, [row['split'] for row in rows]):
            issue(report, 'FAIL', 'sensor_alignment', 'NPZ and provenance level/split differ')
        allowed = {'train', 'val', 'test'}
        if set(splits.tolist()) != allowed or not np.all((levels >= 0) & (levels <= 10)):
            issue(report, 'FAIL', 'sensor_labels', 'Expected train/val/test and levels 0..10')
        groups = data['group_ids'].astype(str).tolist() if 'group_ids' in data else [row.get('group_id') for row in rows]
        if all(group is not None for group in groups):
            try:
                group_count = assert_group_separation(groups, splits.tolist())
            except ValueError as error:
                issue(report, 'FAIL', 'sensor_group_reuse', str(error))
                group_count = len(set(groups))
        else:
            groups, group_count = None, None
            issue(report, 'WARN', 'sensor_no_groups', 'Distinct random seeds do not constitute independent machine/session groups')
        digests = [__import__('hashlib').sha256(vector.tobytes()).hexdigest() for vector in x]
        for index, (digest, row) in enumerate(zip(digests, rows)):
            if 'feature_sha256' in row and digest != row['feature_sha256']:
                issue(report, 'FAIL', 'sensor_feature_provenance_hash', {'row': index})
                break
        seen = {}
        for digest, split in zip(digests, splits):
            if digest in seen and seen[digest] != split:
                issue(report, 'FAIL', 'sensor_cross_split_duplicate', digest)
                break
            seen[digest] = split
        counts = Counter((str(split), int(level)) for split, level in zip(splits, levels))
        missing = [level for level in protocol['sensor']['levels'] if level not in levels]
        if missing:
            issue(report, 'WARN', 'sensor_missing_levels', missing)
        rotation = [abs(row['joint']['velocity'][0]) / (2 * np.pi) for row in rows]
        if np.ptp(rotation) < 1e-5:
            issue(report, 'WARN', 'sensor_fixed_rpm', 'Operating-speed generalization cannot be checked from this dataset')
        nuisance = [numeric_nuisance(row) for row in rows]
        keys = sorted(set.intersection(*(set(row) for row in nuisance)))
        values = np.asarray([[row[key] for key in keys] for row in nuisance])
        effects = {key: nuisance_difference(values[levels == 0, i], values[levels > 0, i]) for i, key in enumerate(keys)}
        for key, effect in effects.items():
            if effect['smd'] > protocol['audit']['nuisance_smd_warn'] or effect['ks'] > protocol['audit']['nuisance_ks_warn']:
                issue(report, 'WARN', 'sensor_nuisance_association', {'variable': key, **effect})
        cv = nuisance_cv(values, levels > 0, groups, folds=protocol['audit']['cross_validation_folds'])
        if cv.get('mean', 0) > protocol['audit']['sensor_nuisance_auc_warn']:
            issue(report, 'WARN', 'sensor_nuisance_classifier', cv)
        if 'generator_version' not in data:
            issue(report, 'WARN', 'sensor_unversioned', 'No fingerprint binds data to source/config; existing data are legacy data')
        else:
            marker = verify_versioned_marker(root / 'sensor', len(rows), report, 'sensor', protocol)
            if str(data['fingerprint'].item()) != marker.get('fingerprint'):
                issue(report, 'FAIL', 'sensor_npz_fingerprint', 'NPZ and COMPLETE fingerprints differ')
            if str(data['generator_version'].item()) != marker.get('generator_version'):
                issue(report, 'FAIL', 'sensor_npz_version', 'NPZ and COMPLETE generator versions differ')
            provenance_groups = [row.get('group_id') for row in rows]
            if groups != provenance_groups:
                issue(report, 'FAIL', 'sensor_group_alignment', 'NPZ/provenance group IDs differ')
            for npz_name, row_name in [('fault_types', 'fault_type'), ('noise', 'noise'), ('gain', 'gain'), ('load', 'load'),
                                       ('commanded_rpm', 'commanded_rpm'), ('observed_rpm', 'observed_rpm')]:
                if npz_name not in data or not np.array_equal(data[npz_name], [row.get(row_name) for row in rows]):
                    issue(report, 'FAIL', 'sensor_condition_alignment', npz_name)
        report['sensor'] = {'file_sha256': sha256(path), 'provenance_sha256': sha256(root / 'sensor/provenance.jsonl'),
                            'n': len(x), 'groups': group_count, 'counts': {f'{s}/{l}': n for (s, l), n in sorted(counts.items())},
                            'rotation_hz_range': [float(min(rotation)), float(max(rotation))],
                            'nuisance_fields': keys, 'nuisance_effects': effects, 'nuisance_cv': cv,
                            'unique_feature_hashes': len(set(digests))}
        if 'noise' in keys:
            rpm_edges = np.linspace(min(protocol['sensor']['supported_rotation_hz']), max(protocol['sensor']['supported_rotation_hz']), 4)
            noise_edges = np.linspace(min(protocol['sensor']['supported_noise_std']), max(protocol['sensor']['supported_noise_std']), 4)
            cells = Counter()
            for row, split, level in zip(nuisance, splits, levels):
                rpm_bin = int(np.clip(np.searchsorted(rpm_edges, row['rotation_hz'], side='right') - 1, 0, 2))
                noise_bin = int(np.clip(np.searchsorted(noise_edges, row['noise'], side='right') - 1, 0, 2))
                cells[str(split), rpm_bin, noise_bin, int(level)] += 1
            report['sensor']['coverage_cells'] = {f'{s}/rpm{r}/noise{n}/level{l}': amount for (s, r, n, l), amount in sorted(cells.items())}
            report['sensor']['coverage_bin_edges'] = {'rotation_hz': rpm_edges.tolist(), 'noise': noise_edges.tolist()}
            test_missing = [f'rpm{r}/noise{n}/level{l}' for r in range(3) for n in range(3) for l in range(11)
                            if not cells.get(('test', r, n, l), 0)]
            if test_missing:
                issue(report, 'WARN', 'sensor_test_coverage_gaps', test_missing)


def scene_nuisance(scene):
    """Camera/material/background nuisance values; exclude the labeled defect geometry."""
    result = {key: float(scene[key]) for key in ['light', 'x', 'y', 'yaw'] if key in scene}
    for key in ['camera_pose', 'product_color', 'background_color']:
        result.update({f'{key}_{i}': float(value) for i, value in enumerate(scene.get(key, []))})
    if isinstance(scene.get('visibility_policy'), dict) and 'color_scale' in scene['visibility_policy']:
        result['visibility_color_scale'] = float(scene['visibility_policy']['color_scale'])
    return result


def audit_vision(root, report, protocol):
    """Hash all images and labels and independently test nuisance predictability."""
    import cv2
    root = root / 'vision'
    records = [json.loads(line) for line in (root / 'manifest.jsonl').read_text().splitlines() if line]
    paths, hashes, ids = set(), set(), set()
    counts = Counter()
    class_runs = defaultdict(list)
    previous_capture_sequence = None
    for row in records:
        image = root / row['image']
        scene = row['scene']
        split, class_id = scene['split'], int(scene['class_id'])
        if split not in {'train', 'val', 'test'} or class_id not in {-1, 0, 1, 2}:
            issue(report, 'FAIL', 'vision_label', {'split': split, 'class_id': class_id})
        if not image.resolve().is_relative_to(root.resolve()) or image.parent.name != split:
            issue(report, 'FAIL', 'vision_path', row['image'])
            continue
        digest = sha256(image)
        decoded = cv2.imread(str(image))
        if decoded is None or decoded.ndim != 3 or decoded.shape[2] != 3:
            issue(report, 'FAIL', 'vision_unreadable_image', row['image'])
        if digest != row['sha256']:
            issue(report, 'FAIL', 'vision_hash_mismatch', row['image'])
        if row['image'] in paths or digest in hashes or scene['scene_id'] in ids:
            issue(report, 'FAIL', 'vision_duplicate', row['image'])
        paths.add(row['image']); hashes.add(digest); ids.add(scene['scene_id'])
        label_path = root / 'labels' / split / (image.stem + '.txt')
        values = label_path.read_text().split()
        if row.get('generator_version') == 'v2':
            if row.get('label') != label_path.relative_to(root).as_posix() or row.get('label_sha256') != sha256(label_path):
                issue(report, 'FAIL', 'vision_label_hash_or_path', row['image'])
        label_ok = not values if class_id == -1 else len(values) == 5 and int(values[0]) == class_id
        if values and label_ok:
            coords = np.asarray(values[1:], float)
            label_ok = np.isfinite(coords).all() and np.all((coords >= 0) & (coords <= 1)) and np.all(coords[2:] > 0)
            label_ok = label_ok and coords[0] - coords[2] / 2 >= -1e-6 and coords[0] + coords[2] / 2 <= 1 + 1e-6
            label_ok = label_ok and coords[1] - coords[3] / 2 >= -1e-6 and coords[1] + coords[3] / 2 <= 1 + 1e-6
        if not label_ok:
            issue(report, 'FAIL', 'vision_bbox', row['image'])
        counts[split, class_id] += 1
        class_runs[split].append(class_id)
        if row.get('generator_version') == 'v2':
            valid_sync = verify_capture_sync(row.get('capture_sync'), scene['scene_id'], row['camera_sim_time'],
                                            row['joint']['sim_time'], report, row['image'])
            if valid_sync:
                sequence = row['capture_sync']['image_sequence']
                if previous_capture_sequence is not None and sequence <= previous_capture_sequence:
                    issue(report, 'FAIL', 'vision_capture_sequence_order', row['image'])
                previous_capture_sequence = sequence
    marker_text = (root / 'COMPLETE').read_text().strip()
    if marker_text.startswith('{'):
        marker = verify_versioned_marker(root, len(records), report, 'vision', protocol)
        expected_manifest = marker.get('files', {}).get('manifest.jsonl')
        if expected_manifest != sha256(root / 'manifest.jsonl'):
            issue(report, 'FAIL', 'vision_marker_manifest_mismatch', 'COMPLETE does not match manifest bytes')
        report['vision_completion'] = marker
        verify_bulk_render_pairs(root, records, marker, report, protocol)
    else:
        if int(marker_text) != len(records):
            issue(report, 'FAIL', 'vision_completion_count', {'marker': marker_text, 'manifest': len(records)})
        issue(report, 'WARN', 'vision_unversioned', 'Legacy count-only COMPLETE does not establish source/config freshness')
    groups = [row['scene'].get('group_id', row.get('group_id')) for row in records]
    if all(group is not None for group in groups):
        try:
            group_count = assert_group_separation(groups, [row['scene']['split'] for row in records])
        except ValueError as error:
            issue(report, 'FAIL', 'vision_group_reuse', str(error))
            group_count = len(set(groups))
    else:
        groups, group_count = None, None
        issue(report, 'WARN', 'vision_no_groups', 'Same primitive renderer remains shared across seed-separated splits')
    # Long, class-homogeneous capture runs can couple rendering drift with class labels.
    switches = {split: int(np.sum(np.diff(classes) != 0)) for split, classes in class_runs.items()}
    for split, count in switches.items():
        if count <= 3:
            issue(report, 'WARN', 'vision_class_order_blocks', {'split': split, 'class_switches': count})
    nuisance = [scene_nuisance(row['scene']) for row in records]
    keys = sorted(set.intersection(*(set(row) for row in nuisance)))
    values = np.asarray([[row[key] for key in keys] for row in nuisance])
    labels = np.asarray([row['scene']['class_id'] for row in records])
    effects = {f'{cls}/{key}': nuisance_difference(values[labels == -1, i], values[labels == cls, i])
               for cls in [0, 1, 2] for i, key in enumerate(keys)}
    warnings = [key for key, effect in effects.items() if effect['smd'] > protocol['audit']['nuisance_smd_warn']
                or effect['ks'] > protocol['audit']['nuisance_ks_warn']]
    if warnings:
        issue(report, 'WARN', 'vision_nuisance_associations', warnings)
    cv = nuisance_cv(values, labels, groups, binary=False, folds=protocol['audit']['cross_validation_folds'])
    if cv.get('mean', 0) > protocol['audit']['vision_nuisance_balanced_accuracy_warn']:
        issue(report, 'WARN', 'vision_nuisance_classifier', cv)
    report['vision'] = {'n': len(records), 'groups': group_count, 'unique_hashes': len(hashes),
                        'manifest_sha256': sha256(root / 'manifest.jsonl'),
                        'counts': {f'{s}/{c}': n for (s, c), n in sorted(counts.items())},
                        'class_switches': switches, 'nuisance_fields': keys,
                        'nuisance_effects': effects, 'nuisance_cv': cv,
                        'excluded_from_model_input': ['filename', 'class_id', 'scene_id', 'timestamp', 'split', 'group_id']}
    domains = {}
    for key in ['camera_bin', 'light_bin', 'background_bin', 'material_id', 'geometry_family']:
        if all(key in row['scene'] for row in records):
            table = Counter((row['scene']['split'], str(row['scene'][key]), row['scene']['class_id']) for row in records)
            domains[key] = {f'{split}/{value}/{cls}': amount for (split, value, cls), amount in sorted(table.items())}
    report['vision']['domain_class_counts'] = domains


def audit(root, protocol_path=PROTOCOL):
    """Return inspectable evidence even if one data modality is malformed."""
    protocol, fingerprint = load_protocol(protocol_path)
    report = {'protocol_id': protocol['protocol_id'], 'protocol_sha256': fingerprint,
              'data_root': str(Path(root)), 'findings': [], 'real_factory_validation': 'UNVALIDATED',
              'interpretation': 'Integrity and synthetic shortcut checks cannot establish bias-free data'}
    for name, function in [('sensor', audit_sensor), ('vision', audit_vision)]:
        try:
            function(Path(root), report, protocol)
        except (KeyError, ValueError, TypeError, IndexError, OSError, AssertionError) as error:
            issue(report, 'FAIL', name + '_audit_exception', f'{type(error).__name__}: {error}')
    issue(report, 'UNVALIDATED', 'real_factory_data_absent', 'No independent real sensor or camera dataset is evaluated')
    report['status'] = 'FAIL' if any(item['severity'] == 'FAIL' for item in report['findings']) else (
        'WARN' if any(item['severity'] == 'WARN' for item in report['findings']) else 'PASS_SYNTHETIC_CHECKS_ONLY')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, default=PROTOCOL)
    args = parser.parse_args()
    result = audit(args.data, args.protocol)
    write_report(args.output, result)
    print(json.dumps({'status': result['status'], 'findings': result['findings']}, indent=2))
    raise SystemExit(2 if result['status'] == 'FAIL' else 0)
