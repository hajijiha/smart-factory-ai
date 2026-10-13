"""Target-product filtering excludes equipment detections without hiding invalid bounds."""
import pytest
from factory.vision.service import product_detections, inspection_identity


def box(center, kind='contamination'):
    """Create an opaque detector box around a chosen image-pixel center."""
    x, y = center
    return {'bbox': [x - 1, y - 1, x + 1, y + 1], 'defect_type': kind, 'confidence': .9}


def test_equipment_and_neighbor_predictions_do_not_classify_target_plate():
    """Whole-frame false positives stay available while selected-unit results exclude them."""
    detections = [box((12, 20)), box((150, 150), 'scratch'), box((300, 150))]
    selected = product_detections(detections, [80, 80, 240, 240], (320, 320, 3))
    assert selected == [detections[1]]
    assert len(detections) == 3


def test_observed_roi_includes_boundary_centers_and_excludes_immediate_outside():
    """The boundary rule is explicit, including boxes crossing the plate edge."""
    detections = [box((80, 80)), box((240, 240)), box((79.99, 150)), box((240.01, 150))]
    assert product_detections(detections, [80, 80, 240, 240], (320, 320, 3)) == detections[:2]


@pytest.mark.parametrize('roi', [None, [], [80, 80, 80, 240], [-1, 80, 240, 240],
                               [80, 80, 321, 240], [80, 80, float('nan'), 240], [True, 80, 240, 240]])
def test_invalid_product_bounds_never_become_normal_inspection(roi):
    """Even zero detections cannot authorize a normal result with malformed identity bounds."""
    with pytest.raises(ValueError):
        product_detections([], roi, (320, 320, 3))


def test_invalid_detector_box_is_rejected_instead_of_silently_hidden():
    """Corrupt detector output cannot masquerade as an equipment exclusion."""
    with pytest.raises(ValueError):
        product_detections([{'bbox': [100, 100, float('nan'), 120]}], [80, 80, 240, 240], (320, 320, 3))


def test_pose_alignment_provenance_survives_quality_and_cam_identity_copy():
    """Observation timing is preserved without copying private simulator labels."""
    message = {'event_id': 'e', 'timestamp': '2026-10-13T00:00:00Z',
               'pose_alignment': {'approximate': True, 'extrapolated': False},
               'camera_receipt_age_seconds_at_capture': .02, 'sensor_sim_time_offset_seconds': .01,
               'ground_truth_class': 2}
    identity = inspection_identity(message)
    assert identity['pose_alignment'] == message['pose_alignment']
    assert identity['camera_receipt_age_seconds_at_capture'] == .02
    assert identity['sensor_sim_time_offset_seconds'] == .01
    assert 'ground_truth_class' not in identity
