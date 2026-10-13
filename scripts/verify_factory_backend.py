"""Transactional live-history oracle; rolls back fixture rows and preserves real data."""
import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import psycopg
from psycopg.rows import dict_row

from factory.common import DATA, REPORTS, utc_now
from factory.settings import database_url
from factory.storage import ensure_optional_schema, store


def product_row(db, identity):
    """Read one fixture product by the full simulator/product/inspection identity."""
    with db.cursor(row_factory=dict_row) as cursor:
        return cursor.execute('''SELECT * FROM product_history
            WHERE run_id=%s AND product_id=%s AND inspection_id=%s''', identity).fetchone()


def main():
    """Verify arrival ordering, atomic mismatch rejection and legacy image joins."""
    run = 'backend-oracle-' + str(uuid.uuid4())
    stamp = datetime.now(timezone.utc).isoformat()
    checks = []
    DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='backend-oracle-', dir=DATA) as directory:
        image = Path(directory) / 'frame.jpg'
        if not cv2.imwrite(str(image), np.full((32, 32, 3), 127, dtype=np.uint8)):
            raise RuntimeError('Cannot create isolated oracle image')
        image_path = str(image.relative_to(DATA))
        with psycopg.connect(database_url()) as db:
            # The outer transaction rolls back all fixture rows and schema probes.
            # store() uses nested transactions so a rejected message is atomic.
            try:
                ensure_optional_schema(db)
                identity = (run, 'part-a', 'inspection-a')
                base = dict(zip(('run_id', 'product_id', 'inspection_id'), identity))
                base.update(timestamp=stamp, event_id=run + '-sensor-a', sensor_event_id=run + '-sensor-a',
                            camera_timestamp=stamp, camera_sim_time=12.5, model_version='sha256:oracle-a')
                store(db, 'factory/health', {'event_id': base['event_id'], 'timestamp': stamp,
                      'health_index': 95.0, 'status': 'normal', 'run_id': run})
                store(db, 'factory/inspection/event', {**base, 'phase': 'outfeed',
                      'position': [2.0, .45, .58], 'outfeed_lane': 'reject', 'classification': 'defective'})
                assert product_row(db, identity)['outcome'] == 'unknown'
                checks.append('outfeed classification cannot replace missing detector evidence')
                store(db, 'factory/inspection/event', {**base, 'phase': 'entered'})
                store(db, 'factory/inspection/event', {**base, 'phase': 'captured'})
                assert product_row(db, identity)['stage'] == 'outfeed'
                checks.append('late lifecycle events preserve outfeed stage')
                cam = {**base, 'gradcam_path': image_path, 'metadata': {'scope': 'fixture'}}
                store(db, 'factory/gradcam', cam)
                quality = {**base, 'detections': [{'defect_type': 'dent', 'confidence': .9,
                           'bbox': [1, 1, 4, 4]}], 'defective': True, 'image_path': image_path,
                           'inference_ms': 2.0}
                store(db, 'factory/quality', quality)
                row = product_row(db, identity)
                assert row['outcome'] == 'defective' and row['health_index'] == 95
                assert row['gradcam_path'] == image_path and row['quality']['event_id'] == base['event_id']
                assert row['camera_sim_time'] == 12.5 and row['model_version'] == 'sha256:oracle-a'
                assert row['outfeed_position'] == [2.0, .45, .58] and row['outfeed_lane'] == 'reject'
                checks.append('CAM and health before quality reconcile full product identity')
                with db.cursor(row_factory=dict_row) as cursor:
                    original = cursor.execute('SELECT * FROM quality_inspections WHERE event_id=%s', (base['event_id'],)).fetchone()
                assert original['health_index_at_time'] == 95 and original['gradcam_path'] == image_path
                assert bytes(original['image_bytes']) == image.read_bytes()
                checks.append('original event-joined image health and CAM routes remain valid')
                for conflicting in ({**quality, 'event_id': run + '-wrong-event', 'sensor_event_id': run + '-wrong-event'},
                                    {**quality, 'product_id': 'wrong-part'}):
                    try:
                        store(db, 'factory/quality', conflicting)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('Mismatched evidence was not rejected')
                assert product_row(db, identity)['quality']['event_id'] == base['event_id']
                with db.cursor() as cursor:
                    assert cursor.execute('SELECT count(*) FROM quality_inspections WHERE event_id=%s',
                        (run + '-wrong-event',)).fetchone()[0] == 0
                checks.append('conflicting event and product evidence are rejected atomically')
                second_identity = (run, 'part-b', 'inspection-b')
                second = {**base, 'product_id': 'part-b', 'inspection_id': 'inspection-b',
                          'event_id': run + '-sensor-b', 'sensor_event_id': run + '-sensor-b'}
                store(db, 'factory/quality', {**second, 'detections': [], 'defective': False,
                                             'image_path': image_path, 'inference_ms': 2.0})
                assert product_row(db, second_identity)['health_index'] is None
                store(db, 'factory/health', {'event_id': second['event_id'], 'timestamp': stamp,
                      'health_index': 92.0, 'status': 'normal', 'run_id': run})
                store(db, 'factory/gradcam', {**second, 'gradcam_path': image_path, 'metadata': {}})
                row = product_row(db, second_identity)
                assert row['outcome'] == 'normal' and row['health_index'] == 92 and row['gradcam_path'] == image_path
                checks.append('quality before health and CAM also reconciles without cross-product mixing')
            finally:
                db.rollback()
    report = {'timestamp': utc_now(), 'result': 'passed', 'fixture_rows_rolled_back': True,
              'fixture_images_removed': True, 'checks': checks}
    destination = REPORTS / 'factory-web-v1' / 'backend-storage.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
