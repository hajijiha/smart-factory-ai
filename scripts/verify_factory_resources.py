"""Sample CPU and memory of the concurrent Compose services without changing them."""
import argparse
import json
import subprocess
import time
from pathlib import Path


def sample(project):
    """Read one Docker resource snapshot and keep the named factory project only."""
    output = subprocess.check_output(['docker', 'stats', '--no-stream', '--format', '{{json .}}'], text=True)
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    return [dict(name=row['Name'], cpu_percent=float(row['CPUPerc'].rstrip('%')),
                 memory=row['MemUsage'], memory_percent=float(row['MemPerc'].rstrip('%')))
            for row in rows if row['Name'].startswith(project+'-')]


def observe(project, duration, output):
    """Write raw samples and per-service means with Docker's one-core CPU convention."""
    started = time.monotonic()
    readings = []
    while time.monotonic()-started < duration:
        tick = time.monotonic()
        readings.append({'wall_seconds': tick-started, 'services': sample(project)})
        time.sleep(max(0, 10-(time.monotonic()-tick)))
    names = sorted({item['name'] for row in readings for item in row['services']})
    means = {}
    for name in names:
        values = [item['cpu_percent'] for row in readings for item in row['services'] if item['name'] == name]
        means[name] = {'mean_cpu_percent': sum(values)/len(values), 'max_cpu_percent': max(values)}
    result = {'project': project, 'duration_wall_seconds': time.monotonic()-started,
              'cpu_convention': 'Docker: 100% means one logical CPU, not the whole host',
              'services': means, 'samples': readings}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'samples'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default='smart-factory-web-20261013')
    parser.add_argument('--duration', type=float, default=1800)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    observe(arguments.project, arguments.duration, arguments.output)
