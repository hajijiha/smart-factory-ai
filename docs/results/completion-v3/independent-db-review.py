"""Read live Timescale/PostgreSQL evidence in an explicitly read-only transaction."""
import json
import psycopg
from factory.settings import database_url


def review():
    """Check migration, OT/IT event joins, online regression and drift-alarm persistence."""
    with psycopg.connect(database_url(), options='-c default_transaction_read_only=on') as db:
        scalar = lambda sql: db.execute(sql).fetchone()[0]
        values = {
            'read_only': scalar('SHOW default_transaction_read_only'),
            'fault_level_type': scalar("SELECT data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='sensor_readings' AND column_name='fault_level'"),
            'sensor_rows': scalar('SELECT count(*) FROM sensor_readings'),
            'quality_rows': scalar('SELECT count(*) FROM quality_inspections'),
            'prognostics_rows': scalar('SELECT count(*) FROM prognostics_readings'),
            'sensor_level_json_mismatches': scalar("SELECT count(*) FROM sensor_readings WHERE fault_level IS DISTINCT FROM (data->>'fault_level')::double precision"),
            'prognostics_without_health': scalar('SELECT count(*) FROM prognostics_readings p LEFT JOIN health_readings h USING(event_id) WHERE h.event_id IS NULL'),
            'prognostics_health_timestamp_mismatches': scalar('SELECT count(*) FROM prognostics_readings p JOIN health_readings h USING(event_id) WHERE p.timestamp<>h.timestamp'),
            'estimated_rul_rows': scalar("SELECT count(*) FROM prognostics_readings WHERE data->'rul'->>'status'='estimated'"),
            'estimated_rul_wear_rows': scalar("SELECT count(*) FROM prognostics_readings WHERE data->'rul'->>'status'='estimated' AND data ? 'wear_scenario'"),
            'drift_alarm_rows': scalar("SELECT count(*) FROM alarms WHERE source='drift'"),
            'drift_alarms_without_evaluated_threshold_crossing': scalar("SELECT count(*) FROM alarms a LEFT JOIN prognostics_readings p USING(event_id) WHERE a.source='drift' AND (p.event_id IS NULL OR p.data->>'drift_evaluated'<>'true' OR (p.data->'drift'->>'psi')::double precision < (p.data->'drift'->>'threshold')::double precision)"),
            'wear_true_rul_below_zero': scalar("SELECT count(*) FROM prognostics_readings WHERE data ? 'wear_scenario' AND (data->'wear_scenario'->>'true_rul_seconds')::double precision < 0"),
        }
    values['passed'] = values['read_only']=='on' and values['fault_level_type']=='double precision'
    values['passed'] &= all(values[key]==0 for key in ('sensor_level_json_mismatches','prognostics_without_health','prognostics_health_timestamp_mismatches','drift_alarms_without_evaluated_threshold_crossing','wear_true_rul_below_zero'))
    values['passed'] &= values['estimated_rul_wear_rows'] >= 20 and values['drift_alarm_rows'] > 0
    return values


if __name__=='__main__':
    print(json.dumps(review(),indent=2))
