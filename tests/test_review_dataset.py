"""Review generated data independently without modifying reports or the live system."""
import hashlib
import json
from collections import Counter
import numpy as np
import pytest
from factory.common import DATA


@pytest.mark.skipif(not (DATA/'sensor/dataset.npz').exists(), reason='Generated sensor data not present')
def test_sensor_scenario_counts_and_provenance_align():
    """Check held-out counts and exact feature/provenance alignment, not only totals."""
    with np.load(DATA/'sensor/dataset.npz') as data:
        features, levels, splits = data['features'], data['levels'], data['splits']
        assert features.shape == (7000, 9)
        assert np.isfinite(features).all()
        for split, normal, fault in [('train',3500,1400),('val',750,300),('test',750,300)]:
            assert int(((splits == split)&(levels == 0)).sum()) == normal
            assert int(((splits == split)&(levels > 0)).sum()) == fault
        provenance = [json.loads(line) for line in (DATA/'sensor/provenance.jsonl').read_text().splitlines()]
        assert len(provenance) == len(levels)
        assert np.array_equal(levels, np.asarray([row['level'] for row in provenance]))
        assert np.array_equal(splits, np.asarray([row['split'] for row in provenance]))
        assert all(abs(row['joint']['velocity'][0]) > 1 for row in provenance)


@pytest.mark.skipif(not (DATA/'vision/COMPLETE').exists(), reason='Generated Gazebo images not present')
def test_vision_no_image_reuse_and_distinct_camera_scenarios():
    """Verify every image hash, class balance and held-out camera domain expansion."""
    root = DATA/'vision'
    records = [json.loads(line) for line in (root/'manifest.jsonl').read_text().splitlines()]
    assert len(records) == 6000
    paths, hashes, scene_ids = set(), set(), set()
    counts = Counter()
    camera_x = {'train':[], 'val':[], 'test':[]}
    for row in records:
        scene = row['scene']
        image = root/row['image']
        split, class_id = scene['split'], scene['class_id']
        assert image.parent.name == split
        assert row['image'] not in paths
        assert row['sha256'] not in hashes
        assert scene['scene_id'] not in scene_ids
        assert hashlib.sha256(image.read_bytes()).hexdigest() == row['sha256']
        paths.add(row['image']); hashes.add(row['sha256']); scene_ids.add(scene['scene_id'])
        counts[split, class_id] += 1
        camera_x[split].append(abs(scene['camera_pose'][0]))
        label = (root/'labels'/split/(image.stem+'.txt')).read_text().split()
        assert not label if class_id == -1 else len(label) == 5 and int(label[0]) == class_id
    for split, normal, defect in [('train',2100,700),('val',450,150),('test',450,150)]:
        assert counts[split,-1] == normal
        assert all(counts[split, class_id] == defect for class_id in range(3))
    assert max(camera_x['train']) <= .07
    assert max(camera_x['val']) > .07
    assert max(camera_x['test']) > .09
