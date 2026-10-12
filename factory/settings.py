"""Resolve shared configuration identically in containers and local verification tools."""
import os
from pathlib import Path
from urllib.parse import quote
import yaml

ROOT = Path(__file__).resolve().parents[1]
PATH_ENVIRONMENTS = {'data': 'DATA_DIR', 'artifacts': 'ARTIFACT_DIR', 'reports': 'REPORT_DIR'}


def load_configuration():
    """Read the selected YAML and map default container paths for local verification."""
    path = Path(os.environ.get('FACTORY_CONFIG', ROOT / 'config.yaml'))
    configuration = yaml.safe_load(path.read_text(encoding='utf-8'))
    baseline_paths = {}
    for name in PATH_ENVIRONMENTS:
        original = str(configuration['paths'][name])
        default_location = Path(original).expanduser()
        if original.startswith('/app/') and ROOT != Path('/app'):
            default_location = ROOT / original[5:]
        elif not default_location.is_absolute():
            default_location = path.resolve().parent / default_location
        baseline_paths[name] = str(default_location.resolve())
        configuration['paths'][name] = str(default_location.resolve())
    configuration['_baseline_paths'] = baseline_paths
    return configuration


CONFIG = load_configuration()


def configured_path(name, configuration=None):
    """Resolve an effective path while keeping the YAML baseline intact for isolation guards."""
    configuration = configuration or CONFIG
    return Path(os.environ.get(PATH_ENVIRONMENTS[name], configuration['paths'][name])).expanduser().resolve()


DATA = configured_path('data')
ARTIFACTS = configured_path('artifacts')
REPORTS = configured_path('reports')


def verification_path(name):
    """Place fresh verification evidence below the configured verification subdirectory."""
    directory = Path(os.environ.get('VERIFY_REPORT_DIR', REPORTS / CONFIG.get('verification', {}).get('run', 'completion-v3')))
    return directory / name


def dashboard_url():
    """Resolve the operator-facing URL used by local integration verification tools."""
    return os.environ.get('DASHBOARD_URL', CONFIG.get('dashboard', {}).get('url', 'http://localhost:8080'))


def database_settings():
    """Return database identity from environment overrides or the shared YAML."""
    configured = CONFIG.get('database', {})
    defaults = {'host': 'database', 'port': 5432, 'user': 'factory', 'password': 'factory_local_demo', 'name': 'factory'}
    variables = {'host': 'DB_HOST', 'port': 'DB_PORT', 'user': 'DB_USER', 'password': 'DB_PASSWORD', 'name': 'DB_NAME'}
    values = {name: os.environ.get(variables[name], configured.get(name, default)) for name, default in defaults.items()}
    values['port'] = int(values['port'])
    return values


def database_url():
    """Build a PostgreSQL URI unless an explicit DATABASE_URL was provided."""
    if os.environ.get('DATABASE_URL'):
        return os.environ['DATABASE_URL']
    values = database_settings()
    return f"postgresql://{quote(str(values['user']), safe='')}:{quote(str(values['password']), safe='')}@{values['host']}:{values['port']}/{quote(str(values['name']), safe='')}"
