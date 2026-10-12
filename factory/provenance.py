"""Bind fitted models and evaluation reports to immutable generated data."""
import hashlib
import json
import os
from pathlib import Path
import numpy as np


def sha256_file(path):
    """Hash file bytes incrementally, without depending on its absolute location."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_identity(root, modality):
    """Identify the actual sensor archive or vision manifest and completed recipe."""
    root = Path(root) / modality
    version, fingerprint = 'v1', None
    if modality == 'sensor':
        source = root / 'dataset.npz'
        with np.load(source, allow_pickle=False) as archive:
            if 'generator_version' in archive:
                version = str(archive['generator_version'].item())
            if 'fingerprint' in archive:
                fingerprint = str(archive['fingerprint'].item())
    elif modality == 'vision':
        source = root / 'manifest.jsonl'
        marker = root / 'COMPLETE'
        if not marker.exists():
            raise RuntimeError('Vision dataset is incomplete')
        try:
            metadata = json.loads(marker.read_text())
        except (ValueError, UnicodeDecodeError):
            metadata = None  # Original v1 marker was plain text.
        if isinstance(metadata, dict):
            version = str(metadata.get('generator_version', 'v1'))
            fingerprint = metadata.get('fingerprint')
    else:
        raise ValueError('Expected sensor or vision modality')
    if version != 'v1' and not fingerprint:
        raise RuntimeError('Versioned data is missing its generator fingerprint')
    return {'modality': modality, 'generator_version': version,
            'generator_fingerprint': fingerprint, 'data_sha256': sha256_file(source)}


def verify_evaluation_identity(trained, observed):
    """Refuse to silently report a different dataset as the fitted dataset's test."""
    if trained is None:
        return 'legacy_checkpoint_without_dataset_identity'
    if trained != observed:
        raise RuntimeError('Evaluation data differs from fitted model provenance; '
                           'use the independent bias evaluator for cross-domain tests')
    return 'matched'


def require_runtime_generator(trained):
    """Reject mixing a versioned checkpoint and a different simulator recipe."""
    version = (trained or {}).get('generator_version', 'v1')
    expected = os.environ.get('DATA_GENERATOR_VERSION', 'v1')
    if version != expected:
        raise RuntimeError(f'Model generator {version} differs from runtime {expected}')


def write_training_record(artifacts, modality, identity):
    """Record a checkpoint hash and the dataset used to select its weights."""
    artifacts = Path(artifacts)
    report = {'schema_version': 1, 'dataset_identity': identity,
              'checkpoint_sha256': sha256_file(artifacts / f'{modality}.pt')}
    (artifacts / f'{modality}.training.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def read_training_record(artifacts, modality):
    """Validate any versioned sidecar before using its recorded provenance."""
    artifacts = Path(artifacts)
    path = artifacts / f'{modality}.training.json'
    if not path.exists():
        return None
    report = json.loads(path.read_text())
    if report['checkpoint_sha256'] != sha256_file(artifacts / f'{modality}.pt'):
        raise RuntimeError('Checkpoint bytes differ from training record')
    return report['dataset_identity']


def write_export_record(artifacts):
    """Bind an exported graph to its source weights, including legacy weights."""
    artifacts = Path(artifacts)
    report = {'schema_version': 1,
              'checkpoint_sha256': sha256_file(artifacts / 'vision.pt'),
              'onnx_sha256': sha256_file(artifacts / 'vision.onnx')}
    (artifacts / 'vision.export.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def vision_export_current(artifacts):
    """A versioned checkpoint must never reuse a graph from earlier training."""
    artifacts = Path(artifacts)
    graph, record = artifacts / 'vision.onnx', artifacts / 'vision.export.json'
    if not graph.exists():
        return False
    if not record.exists():
        # Preserve original checkpoints; newly fitted checkpoints require a binding.
        return not (artifacts / 'vision.training.json').exists()
    report = json.loads(record.read_text())
    return (report.get('checkpoint_sha256') == sha256_file(artifacts / 'vision.pt')
            and report.get('onnx_sha256') == sha256_file(graph))
