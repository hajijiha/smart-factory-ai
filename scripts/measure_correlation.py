"""Collect actual MQTT/DB-backed fault steps for equipment/quality association."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from scripts.verify_runtime import request, wait, ready


def measure(base, hold_seconds, repeats):
    """Observe repeated non-danger fault levels, preserve events and restore normal."""
    started_time = datetime.now(timezone.utc)
    started = started_time.isoformat()
    rows, steps = {}, []
    ready(base)
    try:
        request(base,'/api/fault',{'fault_level':0})
        wait(base,lambda s:s['latest_health'] and s['latest_health']['status']=='normal'
             and s['line'] and s['line']['fault_level']==0)
        request(base,'/api/reset',{})
        wait(base,lambda s:s['line'] and s['line']['running'])
        # Alternating order reduces confounding from a single monotonic time trend.
        for cycle in range(repeats):
            for level in ([0,2,1,3] if cycle%2==0 else [3,1,2,0]):
                request(base,'/api/fault',{'fault_level':level})
                wait(base,lambda s:s['line'] and s['line']['fault_level']==level)
                step={'cycle':cycle,'level':level,'started_at':time.time()}
                deadline=time.monotonic()+hold_seconds
                while time.monotonic()<deadline:
                    snapshot=request(base,'/api/snapshot')
                    if not snapshot['line']['running']:
                        raise RuntimeError('Safety interlock stopped the line; experiment aborted without overriding it')
                    for row in snapshot['rows']:
                        if datetime.fromisoformat(row['timestamp'].replace('Z','+00:00'))>=started_time:
                            rows[row['event_id']]=row
                    time.sleep(2)
                step['ended_at']=time.time()
                steps.append(step)
                print(f'Cycle {cycle+1}/{repeats}: level {level}, collected {len(rows)} events',flush=True)
    except Exception as error:
        partial=Path(__file__).resolve().parents[1]/'docs/results/correlation-aborted.json'
        partial.write_text(json.dumps({'started_at':started,'aborted':True,'reason':str(error),
            'steps':steps,'rows':list(rows.values()),'override_interlock':False},indent=2))
        raise
    finally:
        request(base,'/api/fault',{'fault_level':0})
        wait(base,lambda s:s['latest_health'] and s['latest_health']['status']=='normal')
        request(base,'/api/reset',{})
    time.sleep(4)  # Allow the asynchronous last quality messages to reach SQL.
    for row in request(base,'/api/snapshot')['rows']:
        if row['event_id'] in rows:
            rows[row['event_id']]=row
    output=Path(__file__).resolve().parents[1]/'docs/results/correlation-observations.json'
    output.write_text(json.dumps({'started_at':started,'hold_seconds':hold_seconds,
        'repeats':repeats,'steps':steps,'rows':list(rows.values()),
        'method':'5-second inspected-event buckets; Pearson, Spearman, positive vibration-leading lag 0–30 seconds',
        'limitations':'Seeded simulator common fault input; serially dependent observations; not evidence of real-factory causality'},indent=2))
    print(f'Observed event data: {output}',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://localhost:8080')
    parser.add_argument('--hold-seconds',type=int,default=30)
    parser.add_argument('--repeats',type=int,default=3)
    args=parser.parse_args()
    measure(args.url,args.hold_seconds,args.repeats)
