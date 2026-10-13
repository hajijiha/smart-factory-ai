"""Idempotent MQTT persistence: TimescaleDB telemetry and PostgreSQL image history."""
import logging
import os
import queue
import time
from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb
from factory.common import DATA, connect
from factory.settings import database_url
from factory.world_state import timestamp, vector


PRODUCT_SCHEMA = '''CREATE TABLE IF NOT EXISTS product_history (
 run_id TEXT NOT NULL, product_id TEXT NOT NULL, inspection_id TEXT NOT NULL,
 stage TEXT NOT NULL DEFAULT 'inspection_pending', outcome TEXT NOT NULL DEFAULT 'unknown',
 entered_at TIMESTAMPTZ, captured_at TIMESTAMPTZ, outfeed_at TIMESTAMPTZ,
 camera_timestamp TIMESTAMPTZ, camera_sim_time DOUBLE PRECISION, sensor_event_id TEXT,
 model_version TEXT, health_index DOUBLE PRECISION, quality JSONB,
 gradcam_path TEXT, gradcam_metadata JSONB, updated_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(run_id, product_id, inspection_id),
 CHECK(outcome IN ('unknown','normal','defective'))
)'''


def ensure_optional_schema(db):
    """Preserve old rows while adding prognostics and allowing continuous synthetic wear."""
    db.execute('CREATE TABLE IF NOT EXISTS prognostics_readings (event_id TEXT PRIMARY KEY, timestamp TIMESTAMPTZ NOT NULL, data JSONB NOT NULL)')
    db.execute(PRODUCT_SCHEMA)
    db.execute('''ALTER TABLE product_history
        ADD COLUMN IF NOT EXISTS outfeed_position JSONB,
        ADD COLUMN IF NOT EXISTS outfeed_lane TEXT,
        ADD COLUMN IF NOT EXISTS outfeed_classification TEXT''')
    db.execute('CREATE INDEX IF NOT EXISTS product_history_updated_idx ON product_history(updated_at DESC)')
    db.execute('CREATE INDEX IF NOT EXISTS product_history_sensor_idx ON product_history(sensor_event_id)')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS product_history_evidence_idx ON product_history(sensor_event_id) WHERE sensor_event_id IS NOT NULL')
    column = db.execute("SELECT data_type FROM information_schema.columns WHERE table_name='sensor_readings' AND column_name='fault_level'").fetchone()
    if column and column[0] == 'integer':
        db.execute('ALTER TABLE sensor_readings ALTER COLUMN fault_level TYPE DOUBLE PRECISION USING fault_level::double precision')


def safe_image(path):
    """Resolve service-generated evidence paths strictly inside the data volume."""
    file = (DATA/path).resolve()
    if not file.is_relative_to(DATA.resolve()):
        raise ValueError('Image path leaves data directory')
    return file.read_bytes()


def store_product(cursor, topic, message):
    """Persist lifecycle/AI evidence under a composite identity without pose writes."""
    identity = tuple(message.get(key) for key in ('run_id', 'product_id', 'inspection_id'))
    if not all(isinstance(item, str) and 0 < len(item) <= 160 for item in identity):
        return  # Legacy single-sample events retain their original tables.
    if timestamp(message.get('timestamp')) is None:
        raise ValueError('Product event requires an aware source timestamp')
    phase = message.get('phase')
    if topic == 'factory/inspection/event' and phase not in ('entered', 'captured', 'outfeed'):
        raise ValueError('Unsupported product lifecycle phase')
    sensor_id = message.get('sensor_event_id', message.get('event_id'))
    if sensor_id:
        conflict = cursor.execute('''SELECT EXISTS(
            SELECT 1 FROM product_history WHERE
            (run_id=%s AND product_id=%s AND inspection_id=%s
             AND sensor_event_id IS NOT NULL AND sensor_event_id<>%s)
            OR (sensor_event_id=%s AND (run_id<>%s OR product_id<>%s OR inspection_id<>%s))) AS conflict''',
            (*identity, sensor_id, sensor_id, *identity)).fetchone()
        if (conflict['conflict'] if isinstance(conflict, dict) else conflict[0]):
            raise ValueError('Inspection event belongs to a different product/evidence identity')
    cursor.execute('''INSERT INTO product_history(run_id,product_id,inspection_id,updated_at)
                      VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING''', (*identity, message['timestamp']))
    cursor.execute('''UPDATE product_history SET
        camera_timestamp=COALESCE(%s,camera_timestamp),camera_sim_time=COALESCE(%s,camera_sim_time),
        sensor_event_id=COALESCE(sensor_event_id,%s),model_version=COALESCE(%s,model_version),
        updated_at=GREATEST(updated_at,%s)
        WHERE run_id=%s AND product_id=%s AND inspection_id=%s
          AND (sensor_event_id IS NULL OR %s::text IS NULL OR sensor_event_id=%s)''',
        (message.get('camera_timestamp'), message.get('camera_sim_time'), sensor_id, message.get('model_version'),
         message['timestamp'], *identity, sensor_id, sensor_id))
    if topic == 'factory/inspection/event':
        # Only this fixed mapping supplies column names; user input cannot enter SQL.
        phase_column = {'entered': 'entered_at', 'captured': 'captured_at', 'outfeed': 'outfeed_at'}[phase]
        cursor.execute(f'''UPDATE product_history SET {phase_column}=COALESCE({phase_column},%s),
            stage=CASE WHEN outfeed_at IS NOT NULL OR %s='outfeed' THEN 'outfeed'
                       WHEN captured_at IS NOT NULL OR %s='captured' THEN 'captured' ELSE 'entered' END
            WHERE run_id=%s AND product_id=%s AND inspection_id=%s''',
                       (message['timestamp'], phase, phase, *identity))
        if phase == 'outfeed':
            position = Jsonb(message['position']) if vector(message.get('position'), 3) else None
            lane = message.get('outfeed_lane') if message.get('outfeed_lane') in ('normal', 'reject', 'unconfirmed', 'unknown') else None
            classification = message.get('classification') if message.get('classification') in ('normal', 'defective', 'unknown') else None
            cursor.execute('''UPDATE product_history SET outfeed_position=COALESCE(%s,outfeed_position),
                outfeed_lane=COALESCE(%s,outfeed_lane),outfeed_classification=COALESCE(%s,outfeed_classification)
                WHERE run_id=%s AND product_id=%s AND inspection_id=%s''',
                (position, lane, classification, *identity))
    elif topic == 'factory/quality':
        quality = {key: value for key, value in message.items() if key != 'image_base64'}
        cursor.execute('''UPDATE product_history SET quality=%s,outcome=%s,
            health_index=(SELECT health_index FROM health_readings WHERE event_id=%s)
            WHERE run_id=%s AND product_id=%s AND inspection_id=%s
              AND (sensor_event_id IS NULL OR sensor_event_id=%s)''',
            (Jsonb(quality), 'defective' if message['detections'] else 'normal', message['event_id'],
             *identity, message['event_id']))
    elif topic == 'factory/gradcam':
        cursor.execute('''UPDATE product_history SET gradcam_path=%s,gradcam_metadata=%s
            WHERE run_id=%s AND product_id=%s AND inspection_id=%s
              AND (sensor_event_id IS NULL OR sensor_event_id=%s)''',
            (message['gradcam_path'], Jsonb(message['metadata']), *identity, message['event_id']))


def store(db, topic, message):
    """Persist one event and reconcile health/Grad-CAM regardless of arrival order."""
    with db.transaction(), db.cursor() as cursor:
        if topic == 'factory/sensor':
            cursor.execute('''INSERT INTO sensor_readings(timestamp,sensor_id,event_id,vibration_x,vibration_y,vibration_z,temperature,fault_level,rpm,data)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (message['timestamp'],message['sensor_id'],message['event_id'],message['vibration_x'],message['vibration_y'],message['vibration_z'],
                 message['temperature'],message['fault_level'],message['rpm'],Jsonb({k:v for k,v in message.items() if k!='waveform'})))
        elif topic == 'factory/health':
            cursor.execute('INSERT INTO health_readings VALUES(%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING',
                (message['event_id'],message['timestamp'],message['health_index'],message['status'],Jsonb(message)))
            cursor.execute('UPDATE quality_inspections SET health_index_at_time=%s WHERE event_id=%s',
                (message['health_index'],message['event_id']))
            cursor.execute('UPDATE product_history SET health_index=%s WHERE sensor_event_id=%s',
                           (message['health_index'], message['event_id']))
        elif topic == 'factory/quality':
            detections = message['detections'] or [{'defect_type':'normal','confidence':1.0,'bbox':None}]
            for index,detection in enumerate(detections):
                cursor.execute('''INSERT INTO quality_inspections(id,event_id,timestamp,image_path,defect_type,confidence,bbox,health_index_at_time,gradcam_path,image_bytes,data)
                VALUES(%s,%s,%s,%s,%s,%s,%s,(SELECT health_index FROM health_readings WHERE event_id=%s),
                (SELECT gradcam_path FROM gradcam_artifacts WHERE event_id=%s),%s,%s) ON CONFLICT(id) DO NOTHING''',
                    (f'{message["event_id"]}-{index}',message['event_id'],message['timestamp'],message['image_path'],detection['defect_type'],
                     detection['confidence'],Jsonb(detection['bbox']),message['event_id'],message['event_id'],safe_image(message['image_path']),Jsonb(message)))
        elif topic == 'factory/gradcam':
            cursor.execute('INSERT INTO gradcam_artifacts VALUES(%s,%s,%s) ON CONFLICT(event_id) DO UPDATE SET gradcam_path=EXCLUDED.gradcam_path,metadata=EXCLUDED.metadata',
                           (message['event_id'],message['gradcam_path'],Jsonb(message['metadata'])))
            cursor.execute('UPDATE quality_inspections SET gradcam_path=%s WHERE event_id=%s',
                           (message['gradcam_path'],message['event_id']))
        elif topic == 'factory/prognostics':
            cursor.execute('INSERT INTO prognostics_readings VALUES(%s,%s,%s) ON CONFLICT(event_id) DO NOTHING',
                           (message['event_id'],message['timestamp'],Jsonb(message)))
        elif topic == 'factory/alarm':
            cursor.execute('INSERT INTO alarms(timestamp,event_id,source,severity,message) VALUES(%s,%s,%s,%s,%s)',
                (message['timestamp'],message.get('event_id'),message['source'],message['severity'],message['message']))
        elif topic == 'factory/line':
            cursor.execute('INSERT INTO line_status VALUES(1,%s,%s) ON CONFLICT(id) DO UPDATE SET timestamp=EXCLUDED.timestamp,data=EXCLUDED.data',
                (message['timestamp'],Jsonb(message)))
        if topic in ('factory/inspection/event', 'factory/quality', 'factory/gradcam'):
            store_product(cursor, topic, message)


def main():
    """Serialize event writes with retry after database restarts."""
    events = queue.Queue(maxsize=512)
    def handler(topic,message):
        """Enqueue incoming events without blocking database operations in MQTT."""
        events.put_nowait((topic,message))
    client = connect('storage',['factory/sensor','factory/health','factory/quality','factory/gradcam','factory/alarm','factory/line','factory/prognostics','factory/inspection/event'],handler)
    db = None
    while True:
        topic,message = events.get()
        while True:
            try:
                if db is None or db.closed:
                    db = psycopg.connect(database_url(),autocommit=True)
                    ensure_optional_schema(db)
                store(db,topic,message)
                break
            except psycopg.OperationalError:
                logging.exception('Database temporarily unavailable; retrying event')
                db = None
                time.sleep(2)
            except Exception:
                logging.exception('Invalid storage event: %s',topic)
                break


if __name__ == '__main__':
    main()
