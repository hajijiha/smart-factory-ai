"""Local FastAPI dashboard reading PostgreSQL and sending controls exclusively by MQTT."""
import json
import os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from factory.common import CONFIG, DATA, connect, publish, utc_now
from factory.analytics import associations
from factory.process import build_process_snapshot, group_inspections

app = FastAPI(title='Smart Factory AI')
STATIC = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=STATIC), name='static')
client = connect('dashboard')


class FaultCommand(BaseModel):
    """Validate operator fault injection in the simulator's supported range."""
    fault_level: int = Field(ge=0,le=10)


@app.get('/',response_class=HTMLResponse)
def home():
    """Return the self-contained dashboard UI."""
    return HTMLResponse((STATIC/'index.html').read_text(encoding='utf-8'), headers={'Cache-Control': 'no-cache'})


@app.get('/api/health')
def health():
    """Verify that database and MQTT transport are reachable."""
    with psycopg.connect(os.environ['DATABASE_URL']) as db:
        db.execute('SELECT 1')
    return {'database':'ok','mqtt':client.is_connected(),'timestamp':utc_now()}


@app.get('/api/snapshot')
def snapshot():
    """Retrieve recent event-joined sensor, health, quality and alarm data."""
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as db:
        rows = db.execute('''SELECT s.timestamp,s.event_id,s.vibration_x,s.temperature,s.fault_level,s.rpm,
                    h.health_index,h.status,q.defective
            FROM (SELECT * FROM sensor_readings ORDER BY timestamp DESC LIMIT 600) s
            LEFT JOIN health_readings h ON h.event_id=s.event_id
            LEFT JOIN (SELECT event_id,bool_or(defect_type<>'normal')::int AS defective FROM quality_inspections GROUP BY event_id) q ON q.event_id=s.event_id
            ORDER BY s.timestamp''').fetchall()
        latest = db.execute('SELECT data FROM health_readings ORDER BY timestamp DESC LIMIT 1').fetchone()
        inspections = db.execute('''WITH recent AS (
            SELECT event_id, max(timestamp) AS timestamp FROM quality_inspections
            GROUP BY event_id ORDER BY timestamp DESC, event_id DESC LIMIT 12
        ) SELECT q.id,q.event_id,q.timestamp,q.image_path,q.defect_type,q.confidence,q.bbox,
                 q.health_index_at_time,q.gradcam_path,(q.data->>'inference_ms')::float AS inference_ms
          FROM quality_inspections q JOIN recent r USING (event_id)
          ORDER BY q.timestamp DESC,q.event_id DESC,q.id''').fetchall()
        alarms = db.execute('SELECT * FROM alarms ORDER BY timestamp DESC LIMIT 15').fetchall()
        line = db.execute('SELECT data FROM line_status WHERE id=1').fetchone()
        counts = db.execute('''SELECT (SELECT count(*) FROM sensor_readings) AS sensors,
                        (SELECT count(DISTINCT event_id) FROM quality_inspections) AS inspections,
                        (SELECT count(DISTINCT event_id) FROM quality_inspections WHERE defect_type<>'normal') AS defects''').fetchone()
    result = {'timestamp':utc_now(),'rows':rows,'latest_health':latest['data'] if latest else None,
            'inspections':inspections,'alarms':alarms,'line':line['data'] if line else None,
            'counts':counts,'correlation':associations(rows), 'mqtt_connected':client.is_connected(),
            'inspection_events':group_inspections(inspections)}
    result['process'] = build_process_snapshot(result)
    return result


def send_command(topic, message, retain=False):
    """Accept a queued MQTT command only when the broker connection is available."""
    if not client.is_connected():
        raise HTTPException(503, 'MQTT connection unavailable')
    info = publish(client, topic, message, retain=retain)
    if info.rc != 0:
        raise HTTPException(503, 'MQTT command could not be queued')


@app.post('/api/fault')
def fault(command: FaultCommand):
    """Publish a fault command asynchronously; simulator acknowledges via line events."""
    send_command('factory/command/fault',{'timestamp':utc_now(),**command.model_dump()},retain=True)
    return {'accepted':True,**command.model_dump()}


@app.post('/api/reset')
def reset():
    """Ask the independent safety controller to validate and reset the interlock."""
    send_command('factory/command/reset',{'timestamp':utc_now()})
    return {'accepted':True,'note':'Controller requires HI >= 80'}


@app.get('/images/{path:path}')
def image(path: str):
    """Serve captured image evidence with strict path traversal protection."""
    file = (DATA/path).resolve()
    if not file.is_relative_to(DATA.resolve()) or not file.is_file() or file.suffix.lower() not in ['.jpg','.png']:
        raise HTTPException(404,'Image not found')
    return FileResponse(file)


@app.get('/api/results')
def results():
    """Expose actual experiment reports when present; absent metrics stay absent."""
    directory = Path(CONFIG['paths']['reports'])
    return {file.stem:json.loads(file.read_text()) for file in directory.glob('*.json')}
