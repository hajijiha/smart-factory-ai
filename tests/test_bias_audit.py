"""Tests that challenge audit failures and counterfactual construction, not model scores."""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from scripts.bias_audit import (independently_compare_pixels, numeric_nuisance, scene_nuisance,
                               verify_bulk_render_pairs, verify_capture_sync, verify_render_gate,
                               verify_scene_application, verify_versioned_marker, verify_visibility_policy)
from scripts.bias_evaluate import independent_wave, transformed
from scripts.bias_metrics import (assert_group_separation, binary_metrics, iou,
                                  nuisance_cv, nuisance_difference, wilson)


def test_normal_false_alarm_is_not_hidden_by_positive_f1():
    """Positive-majority F1 can look good even when every healthy sample alarms."""
    result = binary_metrics([False] * 5 + [True] * 95, [True] * 100)
    assert result['f1'] > .95
    assert result['normal_fpr'] == 1
    assert result['fp'] == 5 and result['tn'] == 0
    assert result['normal_fpr_wilson95'][0] > .5


def test_uncertainty_for_small_cells_and_absent_negative_class():
    """A perfect score from 30 samples still has uncertainty, and missing classes stay null."""
    result = binary_metrics([True] * 30, [True] * 30)
    assert result['recall'] == 1 and result['recall_wilson95'][0] < .90
    assert result['normal_fpr'] is None and result['normal_fpr_wilson95'] is None
    assert wilson(0, 0) is None


def test_group_reuse_and_alignment_are_rejected():
    """Changing random seeds does not make a reused machine/session group independent."""
    assert assert_group_separation(['a', 'a', 'b'], ['train', 'train', 'test']) == 2
    with pytest.raises(ValueError, match='appears'):
        assert_group_separation(['a', 'a'], ['train', 'test'])
    with pytest.raises(ValueError, match='aligned'):
        assert_group_separation(['a'], ['train', 'test'])


def test_nuisance_classifier_detects_a_manufactured_shortcut():
    """The audit must flag label-coded acquisition settings despite unique records."""
    rng = np.random.default_rng(9231)
    labels = np.tile([0, 1], 250)
    metadata = (labels + rng.normal(0, .01, len(labels)))[:, None]
    result = nuisance_cv(metadata, labels)
    assert result['mean'] > .98 and result['folds'] == 5
    assert nuisance_difference(metadata[labels == 0], metadata[labels == 1])['smd'] > 10


def test_matched_background_noise_is_not_severity_dependent():
    """An imbalance counterfactual only changes its harmonic, not broadband noise."""
    healthy = independent_wave(25, 0, .025, 9172, 'imbalance')
    faulty = independent_wave(25, 10, .025, 9172, 'imbalance')
    difference = np.fft.rfft(faulty - healthy)
    frequencies = np.fft.rfftfreq(2048, 1 / 2048)
    assert np.linalg.norm(difference[frequencies > 100]) < 1e-8 * np.linalg.norm(difference)
    assert np.argmax(np.abs(difference)) == 25


def test_normal_noise_control_reproducible_and_changes_only_noise():
    """A healthy, noisier input is explicitly evaluated as a negative, not mislabeled fault."""
    first = independent_wave(25, 0, .008, 4117, 'imbalance')
    again = independent_wave(25, 0, .008, 4117, 'imbalance')
    noisy = independent_wave(25, 0, .050, 4117, 'imbalance')
    assert np.array_equal(first, again)
    assert .038 < np.std(noisy - first) < .046


def test_label_and_outcome_metadata_are_excluded_from_shortcut_classifier():
    """Fault level, filenames and defect geometry are not nuisance-model inputs."""
    sensor = numeric_nuisance({'level': 8, 'fault_type': 'imbalance', 'joint': {'velocity': [100]},
                               'signal_params': {'noise': .05, 'load': 1.1, 'severity': .8}})
    assert set(sensor) == {'rotation_hz', 'noise', 'load'}
    image = scene_nuisance({'class_id': 2, 'scene_id': '2_000', 'light': .8,
                            'defect_scale': 1.2, 'camera_pose': [0, 0, 3, 0, 1.57, 0]})
    assert 'class_id' not in image and 'defect_scale' not in image


def test_iou_distinguishes_right_class_at_wrong_location():
    """Image classification alone is not accepted as a successful defect detection."""
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1
    assert iou([0, 0, 10, 10], [10, 10, 20, 20]) == 0
    assert 0 < iou([0, 0, 10, 10], [5, 5, 15, 15]) < .5


def test_paired_vision_transforms_preserve_spatial_shape_and_do_not_modify_original():
    """Each stress input shares original geometry; the original image remains untouched."""
    source = np.arange(48 * 48 * 3, dtype=np.uint8).reshape(48, 48, 3)
    saved = source.copy()
    for name in ['identity', 'grayscale', 'darken', 'brighten', 'blur', 'jpeg', 'erase_defect']:
        result = transformed(source, name, [15, 15, 25, 25])
        assert result.shape == source.shape and result.dtype == np.uint8
        assert np.array_equal(source, saved)
    gray = transformed(source, 'grayscale')
    assert np.array_equal(gray[:, :, 0], gray[:, :, 2])


def test_completion_marker_is_not_trusted_after_data_changes(tmp_path):
    """A valid-looking COMPLETE cannot bless modified payload bytes."""
    import yaml
    from scripts.bias_metrics import sha256
    project = Path(__file__).resolve().parents[1]
    configuration = yaml.safe_load(Path(os.environ.get('FACTORY_CONFIG', str(project / 'config.yaml'))).read_text())
    settings = {key: configuration[key] for key in ['sensor', 'data_generation'] if key in configuration}
    settings['camera'] = {key: configuration['simulator'][key] for key in ['camera_fov', 'camera_size']}
    sources = ['simulator/scenarios.py', 'simulator/signal.py', 'simulator/world.py',
               'simulator/geometry.py', 'factory/pdm/features.py', 'simulator/plugin/world.cpp',
               'simulator/Dockerfile', 'simulator/start.sh', 'simulator/dataset_protocol.py',
               'simulator/sensor_dataset.py', 'simulator/vision_dataset.py', 'simulator/bridge.py',
               'simulator/preflight.py', 'simulator/render_validation.py']
    binding = {'schema_version': 1, 'source_path': 'simulator/plugin/world.cpp',
               'library_path': 'simulator/libfactory_world.so',
               'source_sha256': sha256(project / 'simulator/plugin/world.cpp'), 'library_sha256': '1' * 64}
    specification = {'schema_version': 2, 'generator_version': 'v2', 'settings': settings,
                     'source_hashes': {key: sha256(project / key) for key in sources}, 'plugin_binding': binding}
    fingerprint = hashlib.sha256(json.dumps(specification, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    data = tmp_path / 'payload.npz'
    data.write_bytes(b'original')
    marker = {**specification, 'fingerprint': fingerprint, 'row_count': 7, 'files': {'payload.npz': sha256(data)}}
    (tmp_path / 'COMPLETE').write_text(json.dumps(marker))
    report = {'findings': []}
    verify_versioned_marker(tmp_path, 7, report, 'sensor')
    assert not report['findings']
    data.write_bytes(b'modified')
    verify_versioned_marker(tmp_path, 7, report, 'sensor')
    assert any(item['code'] == 'sensor_marker_file_hash' for item in report['findings'])


def test_self_consistent_but_stale_generator_identity_is_rejected(tmp_path):
    """Re-hashing a claimed stale source does not make it match current implementation."""
    import yaml
    from scripts.bias_metrics import sha256
    project = Path(__file__).resolve().parents[1]
    configuration = yaml.safe_load(Path(os.environ.get('FACTORY_CONFIG', str(project / 'config.yaml'))).read_text())
    settings = {key: configuration[key] for key in ['sensor', 'data_generation'] if key in configuration}
    settings['camera'] = {key: configuration['simulator'][key] for key in ['camera_fov', 'camera_size']}
    sources = ['simulator/scenarios.py', 'simulator/signal.py', 'simulator/world.py',
               'simulator/geometry.py', 'factory/pdm/features.py', 'simulator/plugin/world.cpp',
               'simulator/Dockerfile', 'simulator/start.sh', 'simulator/dataset_protocol.py',
               'simulator/sensor_dataset.py', 'simulator/vision_dataset.py', 'simulator/bridge.py',
               'simulator/preflight.py', 'simulator/render_validation.py']
    binding = {'schema_version': 1, 'source_path': 'simulator/plugin/world.cpp',
               'library_path': 'simulator/libfactory_world.so',
               'source_sha256': sha256(project / 'simulator/plugin/world.cpp'), 'library_sha256': '1' * 64}
    specification = {'schema_version': 2, 'generator_version': 'v2', 'settings': settings,
                     'source_hashes': {key: sha256(project / key) for key in sources}, 'plugin_binding': binding}
    specification['source_hashes']['simulator/signal.py'] = '0' * 64
    marker = {**specification, 'row_count': 0, 'files': {},
              'fingerprint': hashlib.sha256(json.dumps(specification, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    (tmp_path / 'COMPLETE').write_text(json.dumps(marker))
    report = {'findings': []}
    verify_versioned_marker(tmp_path, 0, report, 'sensor')
    assert any(item['code'] == 'sensor_stale_generator_source' for item in report['findings'])


def test_old_v2_marker_without_plugin_binding_is_not_current_v2(tmp_path):
    """An earlier v2 schema lacking the binary binding must not silently pass."""
    marker = {'schema_version': 2, 'generator_version': 'v2', 'settings': {},
              'source_hashes': {}, 'fingerprint': '0' * 64, 'files': {}, 'row_count': 0}
    (tmp_path / 'COMPLETE').write_text(json.dumps(marker))
    report = {'findings': []}
    verify_versioned_marker(tmp_path, 0, report, 'sensor')
    assert any(item['code'] == 'sensor_incomplete_marker' and 'plugin_binding' in item['detail']
               for item in report['findings'])


def test_independent_pixel_gate_rejects_gross_render_scale_error():
    """An analytical label and unique image hash cannot excuse an oversized render."""
    from scripts.bias_metrics import load_protocol
    criteria = load_protocol()[0]['render_gate']
    before = np.zeros((64, 64, 3), np.uint8)
    wrong = before.copy()
    wrong[5:55, 5:55] = 200
    result = independently_compare_pixels(before, wrong, [20, 20, 30, 30], criteria)
    assert result['status'] == 'FAIL' and result['bbox_iou'] < .10
    assert result['outside_roi_fraction'] > .80


def test_pixel_gate_keeps_raw_iou_when_rounding_tolerance_applies():
    """A small raster mismatch is disclosed, while a large edge error cannot be snapped."""
    from scripts.bias_metrics import load_protocol
    criteria = load_protocol()[0]['render_gate']
    before = np.zeros((32, 32, 3), np.uint8)
    after = before.copy()
    after[10:20, 10:20] = 200
    result = independently_compare_pixels(before, after, [9, 9, 21, 21], criteria)
    assert result['status'] == 'PASS' and result['bbox_iou'] == 1
    assert result['raw_bbox_iou'] < .70 and result['outside_roi_fraction'] == 0


def test_vision_completion_without_render_evidence_binding_is_rejected(tmp_path):
    """v2 vision data cannot pass only its manifest/source checks."""
    from scripts.bias_metrics import load_protocol
    report = {'findings': []}
    verify_render_gate(tmp_path, {'files': {}}, report, load_protocol()[0])
    assert any(item['code'] == 'vision_render_gate_binding' for item in report['findings'])


def good_capture_record():
    """Small, physically ordered capture metadata fixture, without camera/model mocks."""
    return {'scene_id': 'scene-a', 'ack_scene_id': 'scene-a', 'ack_image_sequence': 100,
            'ack_arrival_monotonic': 20., 'ack_sim_time': 10., 'first_fresh_sequence': 103,
            'image_sequence': 105, 'fresh_frames': 3, 'image_arrival_monotonic': 20.40,
            'fresh_frame_sequences': [103, 104, 105], 'fresh_frame_sim_times': [10.11, 10.13, 10.15],
            'capture_monotonic': 20.41, 'settling_wall_seconds': .40,
            'camera_sim_time': 10.15, 'joint_sim_time': 10.20,
            'policy': {'minimum_wall_seconds_after_ack': .25, 'minimum_fresh_frames': 3,
                       'minimum_sim_seconds_after_ack': .10},
            'scene_application_policy': {'application_rounds': 3, 'minimum_seconds_between_ack_and_next_command': .10},
            'scene_applications': [
                {'scene_id': 'scene-a', 'scene_application_id': 'scene-a:transaction',
                 'scene_command_id': f'scene-a:transaction:{index}', 'scene_apply_round': index,
                 'command_monotonic': wall - .01, 'ack_arrival_monotonic': wall, 'ack_sim_time': sim,
                 'ack_image_sequence': seq, 'ack_scope': 'physics_applied_and_visual_messages_enqueued'}
                for index, (wall, sim, seq) in enumerate([(18., 9., 80), (19., 9.5, 90), (20., 10., 100)])]}


def test_fresh_capture_requires_wall_and_simulation_barriers():
    """A fast simulation stamp cannot excuse a frame arriving before transport settles."""
    report = {'findings': []}
    good = good_capture_record()
    assert verify_capture_sync(good, 'scene-a', 10.15, 10.20, report, 'image')
    assert not report['findings']
    stale = {**good, 'image_arrival_monotonic': 20.10, 'settling_wall_seconds': .10}
    assert not verify_capture_sync(stale, 'scene-a', 10.15, 10.20, report, 'image')
    assert report['findings'][-1]['code'] == 'vision_capture_sync_inconsistent'


def test_fresh_frame_counter_and_scene_ack_cannot_be_reused():
    """Three claimed frames, reused sequence numbers or another scene ACK fail audit."""
    good = good_capture_record()
    for changed in [{'fresh_frames': 7}, {'first_fresh_sequence': 99}, {'ack_scene_id': 'old-scene'},
                    {'image_sequence': 104, 'fresh_frames': 2}, {'camera_sim_time': 10.01},
                    {'fresh_frame_sequences': [103, 103, 105]}, {'fresh_frame_sim_times': [10.11, 10.11, 10.15]}]:
        report = {'findings': []}
        assert not verify_capture_sync({**good, **changed}, 'scene-a', 10.15, 10.20, report, 'image')
        assert report['findings'][-1]['severity'] == 'FAIL'


def test_unique_fresh_frames_allow_skipped_sequence_numbers():
    """Callback sequences can skip; count actual distinct timestamped observations."""
    record = {**good_capture_record(), 'first_fresh_sequence': 101, 'image_sequence': 110,
              'fresh_frame_sequences': [101, 105, 110]}
    report = {'findings': []}
    assert verify_capture_sync(record, 'scene-a', 10.15, 10.20, report, 'image')
    assert not report['findings']


def test_scene_application_requires_three_distinct_commands_and_enqueued_scope():
    """Repeated ACKs or a claimed renderer completion cannot replace actual command evidence."""
    import copy
    mutations = ['missing_round', 'reused_command', 'short_interval', 'wrong_transaction', 'render_claim', 'stale_final_ack']
    for mutation in mutations:
        record = copy.deepcopy(good_capture_record())
        if mutation == 'missing_round':
            record['scene_applications'].pop()
        elif mutation == 'reused_command':
            record['scene_applications'][1]['scene_command_id'] = record['scene_applications'][0]['scene_command_id']
        elif mutation == 'short_interval':
            record['scene_applications'][1]['command_monotonic'] = 18.05
        elif mutation == 'wrong_transaction':
            record['scene_applications'][1]['scene_application_id'] = 'another-transaction'
        elif mutation == 'render_claim':
            record['scene_applications'][1]['ack_scope'] = 'renderer_completed'
        elif mutation == 'stale_final_ack':
            record['ack_sim_time'] = 9.5
        report = {'findings': []}
        assert not verify_scene_application(record, 'scene-a', report, mutation)
        assert report['findings'][-1]['severity'] == 'FAIL'


def small_bulk_pair(tmp_path):
    """Actual tiny PNG fixture with manifest, labels and two independently timestamped captures."""
    import copy
    import cv2
    from scripts.bias_metrics import load_protocol, sha256
    from scripts.bias_audit import rendering_criteria
    criteria = rendering_criteria(load_protocol()[0])
    scene = {'scene_id': 'scene-a', 'class_id': 0, 'split': 'train', 'defect_scale': .68,
             'defect_primitives': [{'size': [1, 2, 3]}], 'x': 0, 'y': 0, 'yaw': 0,
             'dx': 0, 'dy': 0, 'defect_yaw': 0, 'camera_pose': [0, 0, 3, 0, 1.57, 0],
             'product_color': [.5] * 3, 'background_color': [.1] * 3, 'material_id': 0,
             'specular': .2, 'light': .8, 'seed': 4, 'group_id': 'group-a', 'condition_id': 'condition-a'}
    normal_scene = {**scene, 'scene_id': 'scene-a-counterpart-normal', 'class_id': -1, 'defect_primitives': []}
    defect_sync = good_capture_record()
    normal_sync = copy.deepcopy(defect_sync)
    normal_sync['scene_id'] = normal_sync['ack_scene_id'] = normal_scene['scene_id']
    for key in ['ack_image_sequence', 'first_fresh_sequence', 'image_sequence']:
        normal_sync[key] -= 30
    normal_sync['fresh_frame_sequences'] = [73, 74, 75]
    for application in normal_sync['scene_applications']:
        application['scene_id'] = normal_scene['scene_id']
        application['scene_application_id'] = normal_scene['scene_id'] + ':transaction'
        application['scene_command_id'] = application['scene_application_id'] + ':' + str(application['scene_apply_round'])
        application['ack_image_sequence'] -= 30
    before = np.zeros((32, 32, 3), np.uint8)
    after = before.copy(); after[10:20, 10:20] = 200
    (tmp_path / 'render-pairs').mkdir()
    for kind, image in [('normal', before), ('defect', after)]:
        assert cv2.imwrite(str(tmp_path / 'render-pairs' / (kind + '.png')), image)
    (tmp_path / 'labels/train').mkdir(parents=True)
    (tmp_path / 'labels/train/0_00000.txt').write_text('0 0.46875 0.46875 0.3125 0.3125\n')
    metrics = independently_compare_pixels(before, after, [10, 10, 20, 20], criteria)
    case = {'pair_id': 'scene-a', 'class_id': 0, 'scale': .68, 'normal_scene': normal_scene, 'defect_scene': scene,
            'normal_capture_sync': normal_sync, 'defect_capture_sync': defect_sync,
            'normal_camera_sim_time': 10.15, 'defect_camera_sim_time': 10.15,
            'normal_joint': {'sim_time': 10.20}, 'defect_joint': {'sim_time': 10.20},
            'normal_image': 'render-pairs/normal.png', 'defect_image': 'render-pairs/defect.png',
            'normal_sha256': sha256(tmp_path / 'render-pairs/normal.png'), 'defect_sha256': sha256(tmp_path / 'render-pairs/defect.png'),
            'fingerprint': '1' * 64, 'criteria': criteria, 'model_used': False, 'image_noise_applied': False,
            'expected_bbox_pixels': [10, 10, 20, 20], 'gt_bbox_xyxy': [10, 10, 20, 20],
            'difference_bbox_xyxy': metrics['observed_bbox_pixels'], 'metrics': metrics}
    row = {'scene': scene, 'image': 'images/train/0_00000.jpg', 'render_pair': case,
           'joint': case['defect_joint'], 'camera_sim_time': 10.15, 'capture_sync': defect_sync,
           'fingerprint': '1' * 64, 'generator_version': 'v2'}
    marker = {'fingerprint': '1' * 64, 'render_pair_count': 1,
              'bulk_render_check': {'status': 'PASS', 'pair_count': 1, 'method': 'all_defect_normal_pairs',
                                    'model_used': False, 'image_noise_applied': False, 'criteria': criteria}}
    return row, marker


def test_bulk_pair_audit_reads_actual_png_and_learning_label(tmp_path):
    """A passing pixel report still fails when the corresponding training label points elsewhere."""
    from scripts.bias_metrics import load_protocol
    row, marker = small_bulk_pair(tmp_path)
    report = {'findings': []}
    verify_bulk_render_pairs(tmp_path, [row], marker, report, load_protocol()[0])
    assert not report['findings'] and report['vision_bulk_render_gate']['cases_recomputed'] == 1
    (tmp_path / 'labels/train/0_00000.txt').write_text('0 0.1 0.1 0.1 0.1\n')
    report = {'findings': []}
    verify_bulk_render_pairs(tmp_path, [row], marker, report, load_protocol()[0])
    assert any(finding['code'] == 'vision_bulk_pair_label_alignment' for finding in report['findings'])


def test_bulk_pair_audit_cannot_skip_positive_rows_or_reuse_pair(tmp_path):
    """Every defect, rather than a favourable sample, must have its own bound raw pair."""
    from scripts.bias_metrics import load_protocol
    row, marker = small_bulk_pair(tmp_path)
    report = {'findings': []}
    verify_bulk_render_pairs(tmp_path, [row, {**row, 'render_pair': None}], marker, report, load_protocol()[0])
    codes = {finding['code'] for finding in report['findings']}
    assert {'vision_bulk_render_binding', 'vision_defect_render_pair_missing', 'vision_bulk_pair_coverage'} <= codes
    report = {'findings': []}
    verify_bulk_render_pairs(tmp_path, [row, row], marker, report, load_protocol()[0])
    assert any(finding['code'] == 'vision_bulk_pair_reused' for finding in report['findings'])


def test_bulk_pair_pass_text_cannot_hide_modified_raw_png(tmp_path):
    """Even a self-consistent hash update must still pass independent pixel measurement."""
    import cv2
    from scripts.bias_metrics import load_protocol, sha256
    row, marker = small_bulk_pair(tmp_path)
    oversized = np.full((32, 32, 3), 200, np.uint8)
    assert cv2.imwrite(str(tmp_path / 'render-pairs/defect.png'), oversized)
    row['render_pair']['defect_sha256'] = sha256(tmp_path / 'render-pairs/defect.png')
    report = {'findings': []}
    verify_bulk_render_pairs(tmp_path, [row], marker, report, load_protocol()[0])
    assert any(finding['code'] == 'vision_render_pixel_consistency' for finding in report['findings'])


def visible_scene_fixture(class_id):
    """Use one policy on different raw class palettes; no detector or pixel selection."""
    product = [.55, .60, .58]
    gap, scale, palette = .30, float(np.random.default_rng(121 + 7 * 43 + 88000).uniform(.65, 1)), .65
    bound = (min(product) - gap) * scale
    raw = [.50, .42, .37]
    primitive = {'color_before_contrast': raw, 'color': (np.asarray(raw) / palette * bound).tolist()}
    return {'class_id': class_id, 'product_color': product, 'seed': 121, 'scene_index': 7,
            'defect_primitives': [] if class_id < 0 else [primitive],
            'visibility_policy': {'name': 'visible_surface_proxy_v1', 'minimum_material_rgb_gap': gap,
                'raw_palette_max': palette, 'color_scale': scale, 'product_minimum_channel': min(product),
                'primitive_color_upper_bound': bound, 'scope': 'Visible synthetic proxies; low contrast unvalidated'}}


def test_visibility_policy_is_the_same_material_rule_for_all_classes():
    """The support rule is validated on normals and every defect class, not a known bad group."""
    settings = {'vision_visibility_policy': 'visible_surface_proxy_v1', 'vision_minimum_material_rgb_gap': .30}
    for class_id in [-1, 0, 1, 2]:
        report = {'findings': []}
        assert verify_visibility_policy(visible_scene_fixture(class_id), settings, report, str(class_id))
        assert not report['findings']


def test_visibility_policy_rejects_forged_bound_mapping_and_raw_palette():
    """A visibility claim cannot hide an unchanged weak-contrast rim or class-specific mapping."""
    settings = {'vision_visibility_policy': 'visible_surface_proxy_v1', 'vision_minimum_material_rgb_gap': .30}
    for mutation in ['bound', 'mapped_color', 'original_palette', 'sampling_range', 'wrong_gap', 'label_coded_scale']:
        scene = visible_scene_fixture(1)
        if mutation == 'bound':
            scene['visibility_policy']['primitive_color_upper_bound'] = .50
        elif mutation == 'mapped_color':
            scene['defect_primitives'][0]['color'] = [.50] * 3
        elif mutation == 'original_palette':
            scene['defect_primitives'][0]['color_before_contrast'] = [.80] * 3
        elif mutation == 'sampling_range':
            scene['visibility_policy']['color_scale'] = .50
        elif mutation == 'wrong_gap':
            scene['visibility_policy']['minimum_material_rgb_gap'] = .10
        elif mutation == 'label_coded_scale':
            scene['visibility_policy']['color_scale'] = .80
            scene['visibility_policy']['primitive_color_upper_bound'] = .20
            scene['defect_primitives'][0]['color'] = (np.asarray(scene['defect_primitives'][0]['color_before_contrast']) / .65 * .20).tolist()
        report = {'findings': []}
        assert not verify_visibility_policy(scene, settings, report, mutation)
        assert any(finding['code'] == 'vision_visibility_policy_consistency' for finding in report['findings'])


def test_visibility_policy_is_required_when_dataset_declares_it():
    """Stale scenes cannot silently inherit the new revision's config claim."""
    report = {'findings': []}
    assert not verify_visibility_policy({'class_id': -1}, {'vision_visibility_policy': 'visible_surface_proxy_v1'}, report, 'stale')
    assert report['findings'][0]['code'] == 'vision_visibility_policy_missing'


def test_visibility_nuisance_input_excludes_defect_palette_and_mapped_outcome():
    """Only the independent scale draw joins the nuisance classifier, not the answer geometry/color."""
    features = scene_nuisance(visible_scene_fixture(2))
    assert features['visibility_color_scale'] == visible_scene_fixture(2)['visibility_policy']['color_scale']
    assert not {'class_id', 'color', 'color_before_contrast', 'primitive_color_upper_bound'} & set(features)
