"""Read clean-container evidence and, only if needed, request its guarded startup reset."""
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from factory.settings import ARTIFACTS, DATA, database_url
from factory.provenance import sha256_file, vision_export_current


def api(route, body=None):
    """Use localhost inside this clean dashboard container, never the existing host port."""
    request = urllib.request.Request('http://127.0.0.1:8080'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


initial = json.loads(Path('/app/.clean-startup-initial.json').read_text())
deadline = time.monotonic()+120
reset_ack = None
while True:
    snapshot = api('/api/snapshot')
    world = snapshot['world']
    cameras = world.get('cameras', {})
    ready = (world['connected'] and all(name in cameras and not cameras[name]['stale']
             for name in ('inspection','overview')) and vision_export_current(ARTIFACTS))
    if ready and world['state']['running']:
        break
    if ready and snapshot['process']['reset_ready'] and reset_ack is None:
        reset_ack = api('/api/reset', {})
    if time.monotonic() >= deadline:
        raise RuntimeError('Clean world/camera/model startup readiness unavailable')
    time.sleep(.5)
run_id = world['state']['run_id']
while True:
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        joined = connection.execute("""SELECT DISTINCT ON(q.event_id)
            q.event_id,q.timestamp,q.image_path,q.image_bytes,q.health_index_at_time,
            q.data quality,s.data sensor,h.data health,p.run_id,p.product_id,p.inspection_id,
            p.sensor_event_id,p.outcome,p.health_index product_health,p.stage,p.outfeed_lane
            FROM quality_inspections q
            JOIN sensor_readings s ON s.event_id=q.event_id
            JOIN health_readings h ON h.event_id=q.event_id
            JOIN product_history p ON p.sensor_event_id=q.event_id
              AND p.run_id=q.data->>'run_id' AND p.product_id=q.data->>'product_id'
              AND p.inspection_id=q.data->>'inspection_id'
            WHERE q.data->>'run_id'=%s ORDER BY q.event_id,q.timestamp LIMIT 3""", (run_id,)).fetchall()
        counts = {name:connection.execute('SELECT COUNT(*) count FROM '+name).fetchone()['count']
                  for name in ('sensor_readings','health_readings','quality_inspections',
                               'product_history','prognostics_readings')}
    if len(joined) == 3:
        break
    if time.monotonic() >= deadline:
        raise RuntimeError('Three clean product/sensor/health/quality DB joins unavailable')
    time.sleep(.5)
evidence = []
for row in joined:
    quality, sensor, health = [row[name] for name in ('quality','sensor','health')]
    digest = sha256_file(DATA/row['image_path'])
    assert digest == quality['input_frame_sha256'] == hashlib.sha256(bytes(row['image_bytes'])).hexdigest()
    assert quality['model_version'] == 'sha256:'+initial['model_hashes']['vision.pt']
    assert sensor['run_id'] == health['run_id'] == row['run_id'] == quality['run_id'] == run_id
    assert quality['event_id'] == quality['sensor_event_id'] == row['sensor_event_id'] == sensor['event_id'] == health['event_id']
    assert quality['product_id'] == row['product_id'] and quality['inspection_id'] == row['inspection_id']
    assert row['health_index_at_time'] is not None and row['product_health'] is not None
    evidence.append({key:row[key] for key in ('event_id','run_id','product_id','inspection_id','outcome','stage','outfeed_lane')})
    evidence[-1].update(image_sha256=digest,model_version=quality['model_version'],
                       camera_timestamp=quality['camera_timestamp'],health_index=row['product_health'])
output = Path('/app/docs/results/factory-web-v1')
output.mkdir(parents=True, exist_ok=True)
camera_frames = {}
for name in ('inspection','overview'):
    with urllib.request.urlopen('http://127.0.0.1:8080/api/camera/'+name, timeout=10) as response:
        content_type, data = response.headers.get('Content-Type'), response.read()
    assert data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9') and content_type.startswith('image/jpeg')
    (output/f'clean-camera-{name}.jpg').write_bytes(data)
    camera_frames[name] = {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'content_type':content_type}
snapshot = api('/api/snapshot')
world = snapshot['world']
after_hashes = {name:sha256_file(ARTIFACTS/name) for name in initial['model_hashes']}
assert after_hashes == initial['model_hashes']
assert not (DATA/'sensor/dataset.npz').exists() and not (DATA/'vision/COMPLETE').exists()
report = {'timestamp':datetime.now(timezone.utc).isoformat(),'initial':initial,'run_id':run_id,
          'checks':{'model_hashes_preserved':True,'automatic_onnx_export_bound_to_frozen_pt':vision_export_current(ARTIFACTS),
                    'fresh_world':world['connected'] and not world['stale'],'ten_observed_products':sum(
                        obj.get('kind')=='product' for obj in world['state']['objects'])==10,
                    'actual_camera_jpegs':len(camera_frames)==2,'three_full_new_database_joins':len(evidence)==3,
                    'no_bulk_dataset_generated':True,'no_training_or_threshold_change':True},
          'source_scope':initial['provenance_scope'],'operator_reset_if_needed':reset_ack,
          'snapshot':world,'database_counts':counts,'joined_inspections':evidence,'cameras':camera_frames,
          'export_binding':json.loads((ARTIFACTS/'vision.export.json').read_text()),'model_hashes_after':after_hashes,
          'limitations':['Local committed checkout, not a public download verification.',
                         'A short startup integration check; no accuracy or sustained-throughput claim.']}
(output/'clean-startup-probe.json').write_text(json.dumps(report,indent=2,default=str,allow_nan=False)+'\n')
print(json.dumps({'checks':report['checks'],'run_id':run_id,'counts':counts},allow_nan=False),flush=True)
