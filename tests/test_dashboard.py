"""Dashboard command acceptance and evidence access without external services."""
import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


@pytest.fixture
def dashboard(monkeypatch):
    """Prevent the dashboard's import from starting an MQTT network thread."""
    from factory import common
    monkeypatch.setattr(common, 'connect', lambda *args, **kwargs: None)
    module = importlib.import_module('factory.dashboard')
    monkeypatch.setattr(module, 'client', SimpleNamespace(is_connected=lambda: True))
    return module


def test_disconnected_broker_never_acknowledges_or_publishes(dashboard, monkeypatch):
    sent = []
    monkeypatch.setattr(dashboard, 'client', SimpleNamespace(is_connected=lambda: False))
    monkeypatch.setattr(dashboard, 'publish', lambda *args, **kwargs: sent.append((args, kwargs)))
    with pytest.raises(HTTPException) as failure:
        dashboard.fault(dashboard.FaultCommand(fault_level=10))
    assert failure.value.status_code == 503
    assert sent == []
    with pytest.raises(HTTPException) as failure:
        dashboard.reset()
    assert failure.value.status_code == 503
    assert sent == []


@pytest.mark.parametrize('rc', [4, 15])
def test_mqtt_queue_failure_never_claims_command_acceptance(dashboard, monkeypatch, rc):
    monkeypatch.setattr(dashboard, 'publish', lambda *args, **kwargs: SimpleNamespace(rc=rc))
    with pytest.raises(HTTPException) as failure:
        dashboard.fault(dashboard.FaultCommand(fault_level=0))
    assert failure.value.status_code == 503


def test_command_acceptance_requires_actual_queue_success(dashboard, monkeypatch):
    sent = []

    def publish(client, topic, message, retain=False):
        sent.append((topic, message, retain))
        return SimpleNamespace(rc=0)

    monkeypatch.setattr(dashboard, 'publish', publish)
    reply = dashboard.fault(dashboard.FaultCommand(fault_level=3))
    assert reply == {'accepted': True, 'fault_level': 3}
    assert sent[0][0] == 'factory/command/fault'
    assert sent[0][1]['fault_level'] == 3 and sent[0][1]['timestamp']
    assert sent[0][2] is True
    reply = dashboard.reset()
    assert reply['accepted'] is True
    assert 'running' not in reply, 'Queued reset is not an observed physical restart'
    assert sent[1][0] == 'factory/command/reset'
    assert sent[1][2] is False


@pytest.mark.parametrize('value', [-1, 11, 3.5])
def test_fault_control_rejects_out_of_range_or_fractional_levels(dashboard, value):
    with pytest.raises(ValidationError):
        dashboard.FaultCommand(fault_level=value)


@pytest.mark.parametrize('value', [0, 10])
def test_fault_control_accepts_both_supported_boundaries(dashboard, value):
    assert dashboard.FaultCommand(fault_level=value).fault_level == value


def test_image_access_serves_only_existing_capture_formats_inside_data(dashboard, monkeypatch, tmp_path):
    data = tmp_path/'data'
    data.mkdir()
    (data/'capture.jpg').write_bytes(b'image fixture')
    (data/'capture.png').write_bytes(b'image fixture')
    (data/'private.txt').write_text('private fixture')
    (tmp_path/'outside.jpg').write_bytes(b'outside fixture')
    monkeypatch.setattr(dashboard, 'DATA', data)
    assert dashboard.image('capture.jpg').path == (data/'capture.jpg').resolve()
    assert dashboard.image('capture.png').path == (data/'capture.png').resolve()
    for path in ['../outside.jpg', 'private.txt', 'missing.jpg']:
        with pytest.raises(HTTPException) as failure:
            dashboard.image(path)
        assert failure.value.status_code == 404
