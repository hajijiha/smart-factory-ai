"""Small, independently testable statistics used by the bias audit and challenge."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np


PROTOCOL = Path(__file__).with_name('bias-protocol.json')


def sha256(path):
    """Hash a file incrementally, without loading model/image archives into memory."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_protocol(path=PROTOCOL):
    """Return the locked protocol and the exact registered file hash."""
    path = Path(path)
    return json.loads(path.read_text()), sha256(path)


def wilson(successes, total):
    """95% Wilson interval for a proportion; no samples means unavailable, not zero."""
    if total == 0:
        return None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def binary_metrics(labels, predictions):
    """Report raw counts, positive-class metrics and class-conditional uncertainty."""
    labels = np.asarray(labels, dtype=bool)
    predictions = np.asarray(predictions, dtype=bool)
    if labels.shape != predictions.shape or labels.ndim != 1:
        raise ValueError('Labels and predictions must be aligned one-dimensional arrays')
    tp = int(np.sum(labels & predictions))
    fn = int(np.sum(labels & ~predictions))
    fp = int(np.sum(~labels & predictions))
    tn = int(np.sum(~labels & ~predictions))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else None
    fpr = fp / (fp + tn) if fp + tn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
    return {'n': len(labels), 'tp': tp, 'fn': fn, 'fp': fp, 'tn': tn,
            'precision': precision, 'recall': recall, 'f1': f1, 'normal_fpr': fpr,
            'recall_wilson95': wilson(tp, tp + fn), 'normal_fpr_wilson95': wilson(fp, fp + tn)}


def nuisance_difference(normal, fault):
    """Standardized mean difference and distribution distance for a nuisance variable."""
    from scipy.stats import ks_2samp
    normal, fault = np.asarray(normal, float), np.asarray(fault, float)
    if not len(normal) or not len(fault):
        return {'smd': None, 'ks': None}
    pooled = math.sqrt((np.var(normal) + np.var(fault)) / 2)
    difference = float(abs(np.mean(normal) - np.mean(fault)))
    smd = difference / pooled if pooled > 1e-12 else (0.0 if difference < 1e-12 else 1e12)
    return {'smd': float(smd), 'ks': float(ks_2samp(normal, fault).statistic),
            'normal_n': len(normal), 'fault_n': len(fault)}


def nuisance_cv(values, labels, groups=None, binary=True, folds=5):
    """Predict labels only from nuisance metadata, with group-aware CV when possible."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import balanced_accuracy_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold, StratifiedGroupKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    values, labels = np.asarray(values, float), np.asarray(labels)
    if values.ndim != 2 or not np.isfinite(values).all():
        raise ValueError('Nuisance matrix must be finite and two-dimensional')
    groups = None if groups is None else np.asarray(groups)
    max_folds = min(folds, int(min(np.unique(labels, return_counts=True)[1])))
    if groups is not None:
        max_folds = min(max_folds, len(np.unique(groups)))
    if max_folds < 2 or len(np.unique(labels)) < 2:
        return {'status': 'UNVALIDATED', 'reason': 'Insufficient classes or groups for CV'}
    splitter = (StratifiedGroupKFold(max_folds, shuffle=True, random_state=19073) if groups is not None
                else StratifiedKFold(max_folds, shuffle=True, random_state=19073))
    scores = []
    for train, test in splitter.split(values, labels, groups):
        if len(np.unique(labels[train])) < 2 or len(np.unique(labels[test])) < 2:
            return {'status': 'UNVALIDATED', 'reason': 'A CV fold lacks a label class'}
        model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1500, class_weight='balanced'))
        model.fit(values[train], labels[train])
        scores.append(float(roc_auc_score(labels[test], model.predict_proba(values[test])[:, 1]) if binary
                            else balanced_accuracy_score(labels[test], model.predict(values[test]))))
    return {'metric': 'roc_auc' if binary else 'balanced_accuracy', 'mean': float(np.mean(scores)),
            'fold_scores': scores, 'folds': max_folds, 'group_aware': groups is not None,
            'chance': 0.5 if binary else 1 / len(np.unique(labels)),
            'nuisance_feature_count': values.shape[1], 'n': len(labels)}


def assert_group_separation(groups, splits):
    """A group can contain many windows/images, but must belong to only one split."""
    if len(groups) != len(splits):
        raise ValueError('Group/split arrays are not aligned')
    assignment = {}
    for group, split in zip(groups, splits):
        if group in assignment and assignment[group] != split:
            raise ValueError(f'Group {group!r} appears in {assignment[group]!r} and {split!r}')
        assignment[group] = split
    return len(assignment)


def iou(left, right):
    """Intersection over union of pixel-space xyxy boxes."""
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    a = max(0, left[2] - left[0]) * max(0, left[3] - left[1])
    b = max(0, right[2] - right[0]) * max(0, right[3] - right[1])
    return intersection / max(a + b - intersection, 1e-12)


def write_report(path, report):
    """Write a standalone report atomically; never overwrite training reports."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.pending')
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)
