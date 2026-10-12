"""Reject silent dataset, checkpoint and exported-graph substitution."""
import json
import numpy as np
import pytest
from factory.provenance import (dataset_identity, verify_evaluation_identity,
    require_runtime_generator, write_training_record, read_training_record,
    write_export_record, vision_export_current)


def test_changed_data_cannot_be_reported_as_same_test(tmp_path):
    sensor = tmp_path / 'sensor'
    sensor.mkdir()
    archive = sensor / 'dataset.npz'
    np.savez(archive, features=[1.], generator_version='v2', fingerprint='recipe-a')
    fitted = dataset_identity(tmp_path, 'sensor')
    assert verify_evaluation_identity(fitted, fitted) == 'matched'
    np.savez(archive, features=[2.], generator_version='v2', fingerprint='recipe-a')
    with pytest.raises(RuntimeError, match='differs'):
        verify_evaluation_identity(fitted, dataset_identity(tmp_path, 'sensor'))


def test_checkpoint_substitution_invalidates_training_record(tmp_path):
    weights = tmp_path / 'vision.pt'
    weights.write_bytes(b'fit-a')
    identity = {'generator_version': 'v2'}
    write_training_record(tmp_path, 'vision', identity)
    assert read_training_record(tmp_path, 'vision') == identity
    weights.write_bytes(b'fit-b')
    with pytest.raises(RuntimeError, match='Checkpoint bytes'):
        read_training_record(tmp_path, 'vision')


def test_retraining_or_graph_substitution_requires_reexport(tmp_path):
    weights, graph = tmp_path / 'vision.pt', tmp_path / 'vision.onnx'
    weights.write_bytes(b'weights-a')
    graph.write_bytes(b'graph-a')
    write_training_record(tmp_path, 'vision', {'generator_version': 'v2'})
    assert not vision_export_current(tmp_path)
    write_export_record(tmp_path)
    assert vision_export_current(tmp_path)
    weights.write_bytes(b'weights-b')
    assert not vision_export_current(tmp_path)
    write_export_record(tmp_path)
    graph.write_bytes(b'graph-b')
    assert not vision_export_current(tmp_path)


def test_versioned_model_cannot_run_against_legacy_signal(monkeypatch):
    monkeypatch.delenv('DATA_GENERATOR_VERSION', raising=False)
    require_runtime_generator(None)
    with pytest.raises(RuntimeError, match='differs'):
        require_runtime_generator({'generator_version': 'v2'})
    monkeypatch.setenv('DATA_GENERATOR_VERSION', 'v2')
    require_runtime_generator({'generator_version': 'v2'})


def test_original_vision_marker_and_graph_remain_usable(tmp_path):
    root = tmp_path / 'vision'
    root.mkdir()
    (root / 'manifest.jsonl').write_text('{}\n')
    (root / 'COMPLETE').write_text('6000')
    assert dataset_identity(tmp_path, 'vision')['generator_version'] == 'v1'
    (tmp_path / 'vision.pt').write_bytes(b'original')
    (tmp_path / 'vision.onnx').write_bytes(b'original-graph')
    assert read_training_record(tmp_path, 'vision') is None
    assert vision_export_current(tmp_path)


def test_threshold_cannot_trade_healthy_alarm_budget_for_positive_f1():
    from factory.pdm.train import choose_threshold
    healthy = np.array([0., .1, .2, 10.])
    faults = np.linspace(1., 2., 100)
    scores = np.concatenate([healthy, faults])
    labels = np.concatenate([np.zeros(4, bool), np.ones(100, bool)])
    ordinary = choose_threshold(scores, labels)
    constrained = choose_threshold(scores, labels, normal_fpr_max=.05)
    assert np.mean(healthy > ordinary) == .25
    assert np.mean(healthy > constrained) == 0
