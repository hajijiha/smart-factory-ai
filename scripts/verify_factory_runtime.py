"""Measure the live factory through its public observed-state and product APIs."""
import argparse
import json
import math
import time
from pathlib import Path
from urllib.request import urlopen


def fetch(url):
    """Fetch one JSON response with a bounded network timeout."""
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def percentile(values, fraction):
    """Compute an empirical nearest-rank percentile without extra dependencies."""
    return sorted(values)[min(len(values)-1, math.ceil(len(values)*fraction)-1)] if values else None


def observe(url, duration, output):
    """Record motion, freshness, product integrity and throughput over wall time."""
    started = time.monotonic()
    first_state = last_state = None
    previous = {}
    movements = {}
    serials = set()
    ages = []
    sequences = set()
    runs = set()
    defects = []
    counts = []
    camera_sequences = {'inspection': set(), 'overview': set()}
    camera_ages = {'inspection': [], 'overview': []}
    camera_stale = {'inspection': 0, 'overview': 0}
    stopped_samples = 0
    requests = stale = resets = 0
    next_report = 30
    first_products = fetch(url+'/api/products?limit=500').get('products', [])
    while time.monotonic()-started < duration:
        tick = time.monotonic()
        try:
            envelope = fetch(url+'/api/world')
            requests += 1
            state = envelope.get('state')
            for camera, metadata in envelope.get('cameras', {}).items():
                if camera in camera_sequences and metadata:
                    camera_sequences[camera].add((metadata.get('run_id'), metadata.get('sequence')))
                    if metadata.get('age_ms') is not None:
                        camera_ages[camera].append(metadata['age_ms'])
                    camera_stale[camera] += bool(metadata.get('stale'))
            if not state or envelope.get('stale') or not envelope.get('connected'):
                stale += 1
            if state:
                first_state = first_state or state
                last_state = state
                runs.add(state['run_id'])
                sequences.add((state['run_id'], state['sequence']))
                if envelope.get('age_ms') is not None:
                    ages.append(envelope['age_ms'])
                products = [item for item in state['objects'] if item['kind'] == 'product']
                stopped_samples += state.get('running') is False
                counts.append(len(products))
                identities = [item.get('product_id') for item in products]
                if len(set(identities)) != len(identities) and len(defects) < 30:
                    defects.append({'reason': 'duplicate_live_product_identity', 'sequence': state['sequence']})
                for item in products:
                    name = item['id']
                    xyz = item['position']
                    serial = item.get('product_id', name)
                    serials.add(serial)
                    if xyz[2] < .35 or abs(xyz[1]) > 2:
                        if len(defects) < 30:
                            defects.append({'reason': 'product_left_transport_bounds', 'id': name,
                                            'position': xyz, 'sequence': state['sequence']})
                    if name in previous:
                        old_serial, old_xyz = previous[name]
                        delta = xyz[0]-old_xyz[0]
                        if old_serial == serial and 0 < delta < .5:
                            movements[name] = movements.get(name, 0)+delta
                        if delta < -1:
                            resets += 1
                    previous[name] = (serial, xyz)
                for index, left in enumerate(products):
                    for right in products[index+1:]:
                        gap = [abs(a-b) for a, b in zip(left['position'], right['position'])]
                        if gap[0] < .80 and gap[1] < .58 and gap[2] < .035 and len(defects) < 30:
                            defects.append({'reason': 'plate_overlap', 'ids': [left['id'], right['id']],
                                            'sequence': state['sequence'], 'gap': gap})
        except Exception as error:
            if len(defects) < 30:
                defects.append({'reason': 'http_failure', 'detail': str(error)})
        elapsed = time.monotonic()-started
        if elapsed >= next_report:
            print(f'Observed {elapsed:.0f}s; products={counts[-1] if counts else 0}; '
                  f'moving_bodies={len(movements)}; stale={stale}; integrity_findings={len(defects)}', flush=True)
            next_report += 30
        time.sleep(max(0, .05-(time.monotonic()-tick)))
    elapsed = time.monotonic()-started
    final_products = fetch(url+'/api/products?limit=500').get('products', [])
    result = {'duration_wall_seconds': elapsed, 'http_samples': requests,
              'unique_world_observations': len(sequences), 'observed_state_rate_hz': len(sequences)/elapsed,
              'world_age_mean_ms': sum(ages)/len(ages) if ages else None,
              'world_age_p95_ms': percentile(ages, .95), 'stale_samples': stale,
              'run_ids': sorted(runs), 'product_body_count_min': min(counts) if counts else 0,
              'product_body_count_max': max(counts) if counts else 0,
              'distinct_product_serials': len(serials), 'positive_observed_travel_metres': movements,
              'outfeed_replenishment_resets': resets, 'integrity_findings': defects,
              'sim_time_start': first_state['sim_time'] if first_state else None,
              'sim_time_end': last_state['sim_time'] if last_state else None,
              'products_before': first_products, 'products_after': final_products,
              'stopped_observation_samples': stopped_samples,
              'camera_observations': {name: {
                  'unique_observed_frames': len(camera_sequences[name]),
                  'observed_fps_wall': len(camera_sequences[name])/elapsed,
                  'age_p95_ms': percentile(camera_ages[name], .95),
                  'stale_samples': camera_stale[name]} for name in camera_sequences},
              'scope': 'Gazebo-origin observations via HTTP; display FPS is measured separately in the browser'}
    if first_state and last_state:
        result['real_time_factor'] = (last_state['sim_time']-first_state['sim_time'])/elapsed
        if first_state['run_id'] == last_state['run_id']:
            result['source_state_rate_hz'] = (last_state['sequence']-first_state['sequence'])/elapsed
    result['passed'] = bool(counts and min(counts) >= 2 and len(movements) >= min(counts)
                            and not defects and stale/requests < .02)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ('products_before', 'products_after')}, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://dashboard:8080')
    parser.add_argument('--duration', type=float, default=1800)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    report = observe(arguments.url.rstrip('/'), arguments.duration, arguments.output)
    raise SystemExit(0 if report['passed'] else 1)
