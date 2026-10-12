"""Ensure relocated datasets, reports, database credentials and isolation guards agree."""
from pathlib import Path
from urllib.parse import urlsplit, unquote
from factory import settings
from simulator.dataset_protocol import isolated_directory


def test_environment_paths_are_shared_without_erasing_the_baseline(tmp_path, monkeypatch):
    """An explicit experiment path reaches callers while the original path remains protected."""
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'experiment'))
    monkeypatch.setenv('REPORT_DIR', str(tmp_path / 'evidence'))
    configuration = settings.load_configuration()
    assert settings.configured_path('data', configuration) == (tmp_path / 'experiment').resolve()
    assert settings.configured_path('reports', configuration) == (tmp_path / 'evidence').resolve()
    assert Path(configuration['_baseline_paths']['data']) != settings.configured_path('data', configuration)
    isolated_directory(settings.configured_path('data', configuration), configuration)


def test_database_identity_overrides_are_uri_encoded(monkeypatch):
    """Reserved characters in credentials cannot change the generated host or database."""
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('DB_USER', 'demo user')
    monkeypatch.setenv('DB_PASSWORD', 'p?a ss')
    monkeypatch.setenv('DB_NAME', 'test database')
    monkeypatch.setenv('DB_HOST', 'local-test-host')
    parts = urlsplit(settings.database_url())
    assert parts.hostname == 'local-test-host'
    assert unquote(parts.username) == 'demo user' and unquote(parts.password) == 'p?a ss'
    assert unquote(parts.path) == '/test database'


def test_verification_outputs_do_not_default_to_frozen_v1_evidence(tmp_path, monkeypatch):
    """New integration outputs use the selected run directory instead of old result files."""
    monkeypatch.setenv('VERIFY_REPORT_DIR', str(tmp_path))
    assert settings.verification_path('storage.json') == tmp_path / 'storage.json'
    monkeypatch.setenv('DASHBOARD_URL', 'http://localhost:9087')
    assert settings.dashboard_url() == 'http://localhost:9087'
