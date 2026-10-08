"""Exercise the real dashboard, asynchronous diagnosis and physical conveyor interlock."""
import argparse
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone


def request(base, path, payload=None):
    """Call a local API endpoint and decode its JSON response."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as response:
        return json.load(response)


def wait(base, predicate, timeout=90):
    """Poll measured system state until an explicit condition is satisfied."""
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            snapshot = request(base,'/api/snapshot')
            if predicate(snapshot):
                return snapshot
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass  # Compose starts containers before cold-start imports are ready.
        time.sleep(2)
    raise TimeoutError('Runtime condition not met')


def ready(base, timeout=90):
    """Wait for the actual API, database and MQTT connection before sending controls."""
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            health=request(base,'/api/health')
            snapshot=request(base,'/api/snapshot')
            latest=snapshot['latest_health']
            line=snapshot['line']
            if latest and line:
                ages=[(datetime.now(timezone.utc)-datetime.fromisoformat(item['timestamp'].replace('Z','+00:00'))).total_seconds()
                      for item in [latest,line]]
                if health['database']=='ok' and health['mqtt'] and all(0<=age<=5 for age in ages):
                    return health
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(2)
    raise TimeoutError('Dashboard/MQTT or fresh simulator/PdM observations not ready')


def verify(base):
    """Observe normal, danger stop, normal recovery and explicit safe restart."""
    report = {'started_at':time.time(),'health':ready(base)}
    request(base,'/api/fault',{'fault_level':0})
    normal = wait(base,lambda s:s['latest_health'] and s['latest_health']['status']=='normal'
                   and s['line'] and s['line']['fault_level']==0)
    report['normal'] = {'health':normal['latest_health'],'line':normal['line']}
    request(base,'/api/reset',{})
    wait(base,lambda s:s['line'] and s['line']['running'])
    baseline=normal['counts']
    flowing=wait(base,lambda s:s['counts']['inspections']>baseline['inspections']
                   and s['counts']['sensors']>baseline['sensors'])
    report['pipeline_growth']={'before':baseline,'after':flowing['counts']}
    request(base,'/api/fault',{'fault_level':10})
    danger = wait(base,lambda s:s['latest_health'] and s['latest_health']['status']=='danger' and s['line'] and not s['line']['running'] and abs(s['line']['roller_velocity'])<.01)
    report['danger_stop'] = {'health':danger['latest_health'],'line':danger['line'],'alarms':danger['alarms']}
    request(base,'/api/reset',{})
    time.sleep(3)
    rejected = request(base,'/api/snapshot')
    assert not rejected['line']['running'],'Unsafe reset was accepted'
    report['unsafe_reset_rejected'] = True
    request(base,'/api/fault',{'fault_level':0})
    wait(base,lambda s:s['latest_health'] and s['latest_health']['status']=='normal')
    request(base,'/api/reset',{})
    recovered = wait(base,lambda s:s['line'] and s['line']['running'] and abs(s['line']['roller_velocity'])>.5)
    report['recovery'] = {'line':recovered['line'],'counts':recovered['counts']}
    report['passed'] = True
    output = Path(__file__).resolve().parents[1]/'docs/results/integration.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2))
    print(f'Integration verified: {output}')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://localhost:8080')
    verify(parser.parse_args().url)
