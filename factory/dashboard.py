"""Local FastAPI dashboard reading PostgreSQL and sending controls exclusively by MQTT."""
import asyncio
import json
import os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from factory.common import CONFIG, DATA, REPORTS, connect, publish, utc_now
from factory.analytics import associations
from factory.process import build_process_snapshot, group_inspections
from factory.settings import database_url
from factory.world_state import WorldCache

app = FastAPI(title='Smart Factory AI')
STATIC = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=STATIC), name='static')
world_cache = WorldCache()
LIVE_TOPICS = ['factory/world/state', 'factory/camera/inspection', 'factory/camera/overview',
               'factory/inspection/event', 'factory/quality', 'factory/gradcam', 'factory/health', 'factory/line']
client = connect('dashboard', LIVE_TOPICS, world_cache.observe)


class FaultCommand(BaseModel):
    """Validate operator fault injection in the simulator's supported range."""
    fault_level: int = Field(ge=0,le=10)


@app.get('/',response_class=HTMLResponse)
@app.get('/factory',response_class=HTMLResponse)
def home():
    """Return the default factory page for direct access and browser refresh."""
    return HTMLResponse((STATIC/'factory.html').read_text(encoding='utf-8'), headers={'Cache-Control': 'no-cache'})


@app.get('/monitor', response_class=HTMLResponse)
def monitor():
    """Keep the original control dashboard at its own stable page URL."""
    return HTMLResponse((STATIC/'index.html').read_text(encoding='utf-8'), headers={'Cache-Control': 'no-cache'})


@app.get('/api/world/layout')
def world_layout():
    """Expose the same versioned equipment geometry used to generate the world."""
    from factory.layout import get_layout
    return get_layout()


@app.get('/api/world')
def world():
    """Expose the latest accepted physical observation and independent freshness."""
    return world_cache.snapshot(client.is_connected())


@app.websocket('/ws/world')
async def world_socket(socket: WebSocket):
    """Relay latest snapshots at 20 Hz with backpressure and no accumulating queue."""
    await socket.accept()
    try:
        while True:
            await asyncio.wait_for(socket.send_json(world()), timeout=5)
            await asyncio.sleep(.05)
    except (WebSocketDisconnect, RuntimeError, asyncio.TimeoutError):
        return


def camera_name(name):
    """Accept only the two configured cameras, never arbitrary topic or file names."""
    if name not in ('inspection', 'overview'):
        raise HTTPException(404, 'Camera not found')


@app.get('/api/camera/{name}')
def camera_frame(name: str):
    """Serve the latest actual JPEG with its source timestamps in response headers."""
    camera_name(name)
    frame = world_cache.camera(name)
    if not frame:
        raise HTTPException(503, 'Waiting for a Gazebo camera frame')
    return Response(frame['bytes'], media_type='image/jpeg', headers={
        'Cache-Control': 'no-store', 'X-Observed-At': frame['timestamp'],
        'X-Sim-Time': str(frame['sim_time']), 'X-Frame-Sequence': str(frame['sequence'])})


@app.get('/api/camera/{name}/stream')
def camera_stream(name: str):
    """Stream each accepted JPEG once; stalled sources remain visibly stalled."""
    camera_name(name)
    async def frames():
        """Avoid frame queues and wait cooperatively for the next actual observation."""
        previous = None
        while True:
            frame = world_cache.camera(name)
            identity = (frame.get('run_id'), frame['sequence'], frame['timestamp']) if frame else None
            if frame and identity != previous:
                previous = identity
                header = (f'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame["bytes"])}\r\n'
                          f'X-Observed-At: {frame["timestamp"]}\r\nX-Sim-Time: {frame["sim_time"]}\r\n\r\n').encode('ascii')
                yield header + frame['bytes'] + b'\r\n'
            await asyncio.sleep(.1)
    return StreamingResponse(frames(), media_type='multipart/x-mixed-replace; boundary=frame',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@app.get('/api/products')
def products(limit: int = 40):
    """Read durable per-product evidence, falling back to bounded live observations."""
    limit = max(1, min(limit, 200))
    with psycopg.connect(database_url(), row_factory=dict_row) as db:
        available = db.execute("SELECT to_regclass('public.product_history') AS name").fetchone()
        entries = db.execute('SELECT * FROM product_history ORDER BY updated_at DESC LIMIT %s', (limit,)).fetchall() if available['name'] else []
    if not entries:
        entries = world_cache.product_snapshot(limit)
    return {'products': entries, 'count': len(entries), 'timestamp': utc_now()}


@app.get('/api/health')
def health():
    """Verify that database and MQTT transport are reachable."""
    with psycopg.connect(database_url()) as db:
        db.execute('SELECT 1')
    return {'database':'ok','mqtt':client.is_connected(),'timestamp':utc_now()}


@app.get('/api/snapshot')
def snapshot():
    """Retrieve recent event-joined sensor, health, quality and alarm data."""
    with psycopg.connect(database_url(),row_factory=dict_row) as db:
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
                 q.health_index_at_time,q.gradcam_path,(q.data->>'inference_ms')::float AS inference_ms,
                 q.data->>'run_id' AS run_id,q.data->>'product_id' AS product_id,
                 q.data->>'inspection_id' AS inspection_id,q.data->>'camera_timestamp' AS camera_timestamp,
                 (q.data->>'camera_sim_time')::float AS camera_sim_time,q.data->>'model_version' AS model_version
          FROM quality_inspections q JOIN recent r USING (event_id)
          ORDER BY q.timestamp DESC,q.event_id DESC,q.id''').fetchall()
        alarms = db.execute('SELECT * FROM alarms ORDER BY timestamp DESC LIMIT 15').fetchall()
        line = db.execute('SELECT data FROM line_status WHERE id=1').fetchone()
        counts = db.execute('''SELECT (SELECT count(*) FROM sensor_readings) AS sensors,
                        (SELECT count(DISTINCT event_id) FROM quality_inspections) AS inspections,
                        (SELECT count(DISTINCT event_id) FROM quality_inspections WHERE defect_type<>'normal') AS defects''').fetchone()
        available = db.execute("SELECT to_regclass('public.prognostics_readings') AS name").fetchone()
        prediction = db.execute('SELECT data FROM prognostics_readings ORDER BY timestamp DESC LIMIT 1').fetchone() if available['name'] else None
    result = {'timestamp':utc_now(),'rows':rows,'latest_health':latest['data'] if latest else None,
            'inspections':inspections,'alarms':alarms,'line':line['data'] if line else None,
            'counts':counts,'correlation':associations(rows), 'mqtt_connected':client.is_connected(),
            'inspection_events':group_inspections(inspections), 'prognostics': prediction['data'] if prediction else None}
    result['world'] = world()
    result['process'] = build_process_snapshot(result)
    if os.environ.get('FACTORY_WORLD_MODE') == 'live':
        ready, reason = world_cache.reset_readiness()
        result['process']['reset_ready'] = ready and client.is_connected()
        result['process']['reset_reason'] = reason
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
    if not client.is_connected():
        raise HTTPException(503, 'MQTT connection unavailable')
    if os.environ.get('FACTORY_WORLD_MODE') == 'live' or world_cache.world:
        ready, reason = world_cache.reset_readiness()
        if not ready:
            raise HTTPException(409, reason)
    command = {'timestamp': utc_now()}
    observation = world_cache.snapshot()['state']
    if observation:
        command['run_id'] = observation['run_id']
    send_command('factory/command/reset', command)
    return {'accepted':True,'note':'Controller requires HI >= 80'}


@app.post('/api/stop')
def stop():
    """Queue an operator stop while leaving actual stop confirmation to Gazebo."""
    send_command('factory/command/conveyor', {'timestamp': utc_now(), 'running': False,
                                           'reason': 'operator_stop'}, retain=True)
    return {'accepted': True, 'note': 'Waiting for observed roller and product stop'}


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
    directory = REPORTS
    return {file.stem:json.loads(file.read_text()) for file in directory.glob('*.json')}
