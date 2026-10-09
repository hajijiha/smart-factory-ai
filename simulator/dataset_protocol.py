"""Refuse stale/partial v2 data and bind datasets to generator source and settings."""
import hashlib
import json
import os
import time
from pathlib import Path

SCHEMA_VERSION = 2
SOURCE_FILES = ('simulator/Dockerfile', 'simulator/start.sh', 'simulator/scenarios.py', 'simulator/dataset_protocol.py', 'simulator/signal.py',
    'simulator/sensor_dataset.py', 'simulator/vision_dataset.py', 'simulator/bridge.py',
    'simulator/world.py', 'simulator/geometry.py', 'simulator/preflight.py', 'simulator/render_validation.py', 'simulator/plugin/world.cpp', 'factory/pdm/features.py')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def specification(configuration, plugin_binding=None):
    root = Path(__file__).resolve().parents[1]
    sources = {name: digest(root / name) for name in SOURCE_FILES}
    settings = {key: configuration[key] for key in ('sensor', 'data_generation') if key in configuration}
    settings['camera'] = {key: configuration['simulator'][key] for key in ('camera_fov', 'camera_size')}
    value = {'schema_version': SCHEMA_VERSION, 'generator_version': 'v2', 'settings': settings, 'source_hashes': sources}
    if plugin_binding is not None:
        value['plugin_binding'] = plugin_binding
    value['fingerprint'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return value


def verify_plugin_binding(timeout=30):
    """Bind current plugin source to the library actually mapped by gzserver.

    Source is mounted read-only, whereas the library/build marker are baked into
    the image. A stale image, changed binary, or a different loaded path fails.
    This guard is called only by opt-in v2 acquisition.
    """
    root = Path(__file__).resolve().parents[1]
    marker = root / 'simulator/plugin-build.json'
    if not marker.is_file():
        raise RuntimeError('Missing plugin build binding; rebuild the simulator image')
    binding = json.loads(marker.read_text())
    if binding.get('schema_version') != 1:
        raise RuntimeError('Unsupported plugin build binding schema')
    for key, expected_path in (('source', 'simulator/plugin/world.cpp'), ('library', 'simulator/libfactory_world.so')):
        if binding.get(f'{key}_path') != expected_path or digest(root / expected_path) != binding.get(f'{key}_sha256'):
            raise RuntimeError(f'Stale plugin {key} binding; rebuild the simulator image')
    pid = os.environ.get('FACTORY_GAZEBO_PID')
    if not pid or not pid.isdigit():
        raise RuntimeError('FACTORY_GAZEBO_PID is required to verify the loaded plugin')
    maps = Path('/proc') / pid / 'maps'
    deadline = time.monotonic() + timeout
    while True:
        mapped = [line.split() for line in maps.read_text().splitlines() if 'libfactory_world.so' in line]
        if mapped:
            library = (root / binding['library_path']).resolve()
            if any(len(row) != 6 or Path(row[5]).resolve() != library or int(row[4]) != library.stat().st_ino for row in mapped):
                raise RuntimeError('gzserver loaded a different or replaced factory plugin library')
            return binding
        if time.monotonic() >= deadline:
            raise RuntimeError('gzserver did not load the fingerprinted factory plugin')
        time.sleep(.05)


def isolated_directory(data, configuration):
    """v2 is opt-in and may never write to the configured v1 data directory."""
    explicit = os.environ.get('DATA_DIR')
    if not explicit or Path(data).resolve() == Path(configuration['paths']['data']).resolve():
        raise RuntimeError('v2 requires an explicit DATA_DIR different from the baseline data directory')


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.pending')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temporary.replace(path)


def cached(root, expected, files):
    """Return true only for complete byte-verified data from this exact generator."""
    root = Path(root)
    marker = root / 'COMPLETE'
    if not root.exists():
        return False
    present = [p for p in root.iterdir() if p.name != '.gitkeep']
    if not present:
        return False
    if not marker.exists():
        raise RuntimeError(f'Partial v2 dataset at {root}; use a new DATA_DIR')
    try:
        record = json.loads(marker.read_text())
    except (ValueError, OSError) as error:
        raise RuntimeError(f'Legacy or invalid COMPLETE at {root}; use a new DATA_DIR') from error
    if record.get('schema_version') != SCHEMA_VERSION or record.get('fingerprint') != expected['fingerprint']:
        raise RuntimeError(f'Stale dataset fingerprint at {root}; use a new DATA_DIR')
    for name in files:
        if not (root / name).is_file() or record.get('files', {}).get(name) != digest(root / name):
            raise RuntimeError(f'Incomplete or modified v2 dataset file: {root / name}')
    return True


def complete(root, expected, files, count, **metadata):
    value = {**expected, 'row_count': int(count), 'files': {name: digest(Path(root) / name) for name in files}, **metadata}
    write_json(Path(root) / 'COMPLETE', value)
    return value
