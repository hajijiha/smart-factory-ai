"""Idempotent MQTT persistence: TimescaleDB telemetry and PostgreSQL image history."""
import logging
import os
import queue
import time
from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb
from factory.common import DATA, connect


def safe_image(path):
    """Resolve service-generated evidence paths strictly inside the data volume."""
    file = (DATA/path).resolve()
    if not file.is_relative_to(DATA.resolve()):
        raise ValueError('Image path leaves data directory')
    return file.read_bytes()


def store(db, topic, message):
    """Persist one event and reconcile health/Grad-CAM regardless of arrival order."""
    with db.cursor() as cursor:
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
        elif topic == 'factory/alarm':
            cursor.execute('INSERT INTO alarms(timestamp,event_id,source,severity,message) VALUES(%s,%s,%s,%s,%s)',
                (message['timestamp'],message.get('event_id'),message['source'],message['severity'],message['message']))
        elif topic == 'factory/line':
            cursor.execute('INSERT INTO line_status VALUES(1,%s,%s) ON CONFLICT(id) DO UPDATE SET timestamp=EXCLUDED.timestamp,data=EXCLUDED.data',
                (message['timestamp'],Jsonb(message)))


def main():
    """Serialize event writes with retry after database restarts."""
    events = queue.Queue(maxsize=512)
    def handler(topic,message):
        """Enqueue incoming events without blocking database operations in MQTT."""
        events.put_nowait((topic,message))
    client = connect('storage',['factory/sensor','factory/health','factory/quality','factory/gradcam','factory/alarm','factory/line'],handler)
    db = None
    while True:
        topic,message = events.get()
        while True:
            try:
                if db is None or db.closed:
                    db = psycopg.connect(os.environ['DATABASE_URL'],autocommit=True)
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
