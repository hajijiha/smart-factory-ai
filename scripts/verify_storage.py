"""Verify actual Timescale/PostgreSQL schema, image bytes and event-aligned HI."""
import hashlib
import json
import subprocess
import time
from pathlib import Path


def sql(statement):
    """Read JSON from the running project's database through Compose, without ports."""
    completed=subprocess.run(['docker','compose','exec','-T','database','psql',
        '-U','factory','-d','factory','-Atc',statement],check=True,capture_output=True,text=True)
    return json.loads(completed.stdout)


def verify():
    """Require real defect evidence, Timescale hypertable and identical event HI."""
    time.sleep(5)
    report=sql("""SELECT json_build_object(
      'sensor_hypertable',EXISTS(SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name='sensor_readings'),
      'sensor_rows',(SELECT count(*) FROM sensor_readings),
      'quality_rows',(SELECT count(*) FROM quality_inspections),
      'defect_rows',(SELECT count(*) FROM quality_inspections WHERE defect_type<>'normal'),
      'defect_types',(SELECT json_agg(DISTINCT defect_type) FROM quality_inspections WHERE defect_type<>'normal'),
      'images_missing',(SELECT count(*) FROM quality_inspections WHERE image_bytes IS NULL OR octet_length(image_bytes)=0),
      'settled_hi_missing',(SELECT count(*) FROM quality_inspections WHERE health_index_at_time IS NULL AND timestamp < NOW()-INTERVAL '5 seconds'),
      'hi_mismatch',(SELECT count(*) FROM quality_inspections q JOIN health_readings h USING(event_id) WHERE q.health_index_at_time<>h.health_index),
      'gradcam_rows',(SELECT count(*) FROM quality_inspections WHERE gradcam_path IS NOT NULL),
      'schema',(SELECT json_object_agg(table_name,fields) FROM
          (SELECT table_name,json_agg(column_name ORDER BY ordinal_position) fields FROM information_schema.columns
           WHERE table_schema='public' AND table_name IN ('sensor_readings','quality_inspections') GROUP BY table_name) s)
      )""")
    assert report['sensor_hypertable'] and report['sensor_rows']>0
    assert report['defect_rows']>0 and report['gradcam_rows']>0
    assert report['images_missing']==report['settled_hi_missing']==report['hi_mismatch']==0
    samples=sql("""SELECT json_agg(s) FROM (SELECT id,image_path,encode(image_bytes,'hex') bytes
         FROM quality_inspections WHERE defect_type<>'normal' ORDER BY timestamp DESC LIMIT 3) s""")
    root=Path(__file__).resolve().parents[1]
    report['image_hash_checks']=[]
    for sample in samples:
        stored=bytes.fromhex(sample['bytes'])
        disk=(root/'data'/sample['image_path']).read_bytes()
        assert stored==disk, 'Stored BYTEA differs from the actual captured JPEG'
        report['image_hash_checks'].append({'id':sample['id'],'sha256':hashlib.sha256(stored).hexdigest(),'bytes':len(stored),'matches_disk':True})
    report.update(passed=True,measured_at=time.time())
    path=root/'docs/results/storage.json'
    path.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    verify()
