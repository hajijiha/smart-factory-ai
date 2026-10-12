"""Read a newly booted dashboard without changing fault levels or sending MQTT events."""
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def request(endpoint):
    """Retrieve a single local HTTP document with a finite network timeout."""
    with urllib.request.urlopen('http://localhost:8085'+endpoint, timeout=8) as response:
        return json.load(response)


def review():
    """Wait for actual model output in fresh DB rather than treating process launch as success."""
    start, errors = time.monotonic(), []
    first, latest, health = None, None, None
    while time.monotonic()-start < 150:
        try:
            health = request('/api/health')
            latest = request('/api/snapshot')
            if first is None:
                first = latest
            prediction = latest.get('prognostics') or {}
            rul = prediction.get('rul') or {}
            if latest['counts']['sensors']>10 and latest['counts']['inspections']>10 and rul.get('status')=='outside_wear_scenario':
                break
        except Exception as exc:
            errors.append(str(exc))
        time.sleep(2)
    failures=[]
    def require(condition, message):
        """Record the actual failed readiness contract while keeping its full snapshot."""
        if not condition:
            failures.append(message)
    require(health is not None and health['database']=='ok' and health['mqtt'] is True, 'DB/MQTT health is not ready')
    if latest:
        h, p = latest.get('latest_health') or {}, latest.get('prognostics') or {}
        require(latest['counts']['sensors']>10 and latest['counts']['inspections']>10, 'No actual sensor/inspection stream')
        require(len(h.get('features',{}))==9 and len(h.get('spectrum',{}).get('frequencies',[]))>0, 'PdM feature/FFT output absent')
        require(0<=h.get('health_index',-1)<=100 and 0<=h.get('anomaly_score',-1)<=1, 'HI/anomaly output outside bounds')
        require(len(latest.get('inspection_events',[]))>0, 'Actual Vision inspection output absent')
        require(p.get('rul',{}).get('status')=='outside_wear_scenario', 'RUL did not complete its 23-second chronological warmup')
        require(p.get('rul',{}).get('rul_seconds') is None, 'Normal stationary equipment should not claim finite lifetime')
        require(p.get('drift',{}).get('status') in ('warming_up','stable'), 'Fixed PSI reference did not initialize normally')
        require(latest.get('line',{}).get('fault_level')==0, 'Observation changed the default fault level')
        require(latest.get('line',{}).get('roller_velocity',0)>.5, 'Fresh physical conveyor is not running')
    else:
        failures.append('No readable snapshot')
    result={'passed':not failures,'failures':failures,'finished_at':datetime.now(timezone.utc).isoformat(),
            'source_commit':'d200c86db86165e15b5daff4be2e4e6da0a71db6','project':'smart-factory-independent-clean-20261013',
            'port':8085,'observed_seconds':time.monotonic()-start,'controls_sent':False,'pre_ready_connection_errors':errors,
            'health':health,'initial_counts':first.get('counts') if first else None,'final_counts':latest.get('counts') if latest else None,
            'rul':(latest.get('prognostics') or {}).get('rul') if latest else None,
            'psi':(latest.get('prognostics') or {}).get('drift') if latest else None,
            'latest_snapshot':latest}
    Path(__file__).with_name('independent-clean-startup.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    result=review()
    print(json.dumps({key:value for key,value in result.items() if key!='latest_snapshot'},indent=2))
    raise SystemExit(0 if result['passed'] else 1)
