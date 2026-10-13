"""Fixed live-run selection and independent detection reference validation."""
import hashlib
import json

import cv2
import numpy as np
import pytest
from scripts.evaluate_live_factory import match_predictions, select_frames, validate_labels, validate_capture_protocol


def protocol_record(index=0):
    """Construct a bounded approximate frame alignment with later PUBACK evidence."""
    return {'timestamp': '2026-10-13T01:00:00Z', 'delivery_acknowledged_at': '2026-10-13T01:00:01Z',
            'camera_sim_time': index+1.05, 'delivery_delay_seconds': .2,
            'camera_receipt_age_seconds_at_capture': .03, 'sensor_sim_time_offset_seconds': .01,
            'pose_alignment': {'method': 'linear_position_and_quaternion_slerp_between_measured_states',
                               'approximate': True, 'extrapolated': False,
                               'before_sim_time': index+1, 'after_sim_time': index+1.1,
                               'camera_sim_time': index+1.05, 'gap_sim_seconds': .1,
                               'before_sequence': index+1, 'after_sequence': index+2,
                               'interpolation_ratio': .5}}


@pytest.mark.parametrize('changes', [{'camera_receipt_age_seconds_at_capture': 2.01},
                                   {'sensor_sim_time_offset_seconds': .2},
                                   {'delivery_acknowledged_at': '2026-10-13T00:59:59Z'},
                                   {'pose_alignment': None}])
def test_capture_protocol_rejects_stale_camera_misaligned_sensor_and_missing_ack(changes):
    """A frame cannot enter metrics without trustworthy bounded capture metadata."""
    with pytest.raises(ValueError):
        validate_capture_protocol({**protocol_record(), **changes})


def test_capture_protocol_rejects_extrapolation_and_false_interpolation_ratio():
    """Numerical bounds alone cannot conceal invented future poses or wrong ratios."""
    record = protocol_record()
    for changes in ({'extrapolated': True}, {'interpolation_ratio': .8}):
        with pytest.raises(ValueError):
            validate_capture_protocol({**record, 'pose_alignment': {**record['pose_alignment'], **changes}})


@pytest.mark.parametrize('label', [[0, .5, .5, 0, .1], [0, .01, .5, .5, .1],
                                 [3, .5, .5, .1, .1], [1.5, .5, .5, .1, .1],
                                 [0, .5, .5, float('nan'), .1]])
def test_invalid_geometric_reference_cannot_enter_new_world_metrics(label):
    """Out-of-view, zero-area, nonfinite and unknown-class labels fail before inference."""
    with pytest.raises(ValueError):
        validate_labels([label])


def test_duplicate_and_wrong_class_detections_do_not_inflate_target_recall():
    """One target can match one correct-class prediction at each IoU threshold."""
    reference = [{'class_id': 0, 'bbox': [10, 10, 20, 20]}]
    predictions = [{'class_id': 0, 'bbox': [10, 10, 20, 20], 'confidence': .8},
                   {'class_id': 0, 'bbox': [10, 10, 20, 20], 'confidence': .9},
                   {'class_id': 1, 'bbox': [10, 10, 20, 20], 'confidence': .95}]
    ordered, correct = match_predictions(predictions, reference, [.5, .95])
    assert ordered[0]['class_id'] == 1
    assert correct.tolist() == [[False, False], [True, True], [False, False]]


def test_selection_uses_first100_of_declared_run_without_mixing_or_cherry_picking(tmp_path):
    """Foreign failed runs and later easy frames cannot alter the locked sample."""
    (tmp_path/'images').mkdir()
    (tmp_path/'labels').mkdir()
    records = [{'run_id': 'failed-poc', 'defect_mode': 'balanced-evaluation'}]
    for index in range(100):
        kind = index % 4 - 1
        image_path = tmp_path/'images'/f'{index}.jpg'
        assert cv2.imwrite(str(image_path), np.full((32, 32, 3), index, dtype=np.uint8))
        labels = [] if kind == -1 else [[kind, .5, .5, .1, .1]]
        label_path = tmp_path/'labels'/f'{index}.txt'
        label_path.write_text(''.join(' '.join(map(str, label))+'\n' for label in labels))
        records.append({**protocol_record(index), 'run_id': 'wanted', 'defect_mode': 'balanced-evaluation',
                        'event_id': f'e-{index}', 'product_id': f'p-{index}', 'inspection_id': f'i-{index}',
                        'image': image_path.relative_to(tmp_path).as_posix(), 'label': label_path.relative_to(tmp_path).as_posix(),
                        'sha256': hashlib.sha256(image_path.read_bytes()).hexdigest(), 'ground_truth_class': kind,
                        'image_labels': labels, 'target_product_labels': labels,
                        'product_roi': [8, 8, 24, 24], 'target_product_roi': [8, 8, 24, 24]})
    records.append({'run_id': 'wanted', 'defect_mode': 'balanced-evaluation', 'event_id': 'later-unreadable'})
    (tmp_path/'manifest.jsonl').write_text(''.join(json.dumps(record)+'\n' for record in records))
    selected, _ = select_frames(tmp_path, 'wanted')
    assert len(selected) == 100
    assert selected[0]['record']['event_id'] == 'e-0'
    assert selected[-1]['record']['event_id'] == 'e-99'
    assert selected[0]['manifest_line'] == 2
    assert all(item['record']['run_id'] == 'wanted' for item in selected)
