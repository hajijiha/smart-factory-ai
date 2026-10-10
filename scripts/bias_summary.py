"""Summarize two frozen-model challenge reports on exactly matching inputs."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from scripts.bias_metrics import load_protocol, sha256, write_report


def load_comparison(root):
    protocol, protocol_hash = load_protocol()
    reports = {}
    for name in ('baseline', 'candidate'):
        path = root / f'{name}-challenge.json'
        report = json.loads(path.read_text())
        frozen = json.loads((root / f'{name}-freeze.json').read_text())
        if report['phase'] != 'final' or report['models'] != frozen['models']:
            raise ValueError(f'{name} is not a frozen final evaluation')
        if report['protocol_sha256'] != frozen['protocol_sha256']:
            raise ValueError(f'{name} protocol differs from its freeze record')
        if (report['protocol_sha256'] != protocol_hash or report['protocol_id'] != protocol['protocol_id']
                or report['seed'] != protocol['seeds']['final']):
            raise ValueError(f'{name} evaluation differs from the registered final protocol')
        if set(report['models']) != {'pdm.pt', 'vision.pt', 'vision.onnx'}:
            raise ValueError(f'{name} evaluation is missing a model')
        if report['sensor']['n'] != 3630 or report['vision']['n_selected'] != 160:
            raise ValueError(f'{name} evaluation has unexpected sample counts')
        validate_vision_rows(report['vision']['raw'], protocol['vision'])
        reports[name] = report
    left, right = reports['baseline'], reports['candidate']
    for key in ('phase', 'seed', 'protocol_id', 'protocol_sha256', 'evaluation_source_sha256', 'environment'):
        if left[key] != right[key]:
            raise ValueError(f'Compared evaluations differ in {key}')
    if left['sensor']['feature_sha256'] != right['sensor']['feature_sha256']:
        raise ValueError('Sensor challenge inputs differ')
    if left['vision']['manifest_sha256'] != right['vision']['manifest_sha256']:
        raise ValueError('Vision challenge manifests differ')
    if left['vision']['inference_config'] != right['vision']['inference_config']:
        raise ValueError('Vision inference configurations differ')
    inputs = lambda report: sorted((row['image'], row['sha256'], row['label_sha256'],
                                   row['transformed_sha256'], tuple(row['input_shape']), row['input_dtype'],
                                   row['class_id'], row['transform'])
                                  for row in report['vision']['raw'])
    if inputs(left) != inputs(right) or len(inputs(left)) != 1080:
        raise ValueError('Selected vision images or transformations differ')
    return reports


def validate_vision_rows(rows, spec):
    by_image = defaultdict(list)
    for row in rows:
        if row['class_id'] not in (-1, 0, 1, 2):
            raise ValueError('Unexpected vision challenge class')
        by_image[row['image']].append(row)
    counts = Counter(group[0]['class_id'] for group in by_image.values())
    if counts != Counter({cls: spec['images_per_class'] for cls in (-1, 0, 1, 2)}):
        raise ValueError('Vision challenge lacks the registered unique images per class')
    for group in by_image.values():
        first = group[0]
        expected = set(spec['transforms']) | ({'erase_defect'} if first['class_id'] >= 0 else set())
        if len(group) != len(expected) or {row['transform'] for row in group} != expected:
            raise ValueError('Duplicate or missing vision transformations')
        for row in group:
            if any(row[key] != first[key] for key in ('class_id', 'sha256', 'label_sha256')):
                raise ValueError('A challenge image has inconsistent source metadata')


def summarize(root):
    reports = load_comparison(root)
    summary = {
        'protocol_id': reports['candidate']['protocol_id'],
        'protocol_sha256': reports['candidate']['protocol_sha256'],
        'seed': reports['candidate']['seed'],
        'same_sensor_and_vision_inputs': True,
        'sensor_windows': 3630, 'vision_selected_images': 160, 'vision_evaluations': 1080,
        'real_factory_validation': 'UNVALIDATED',
        'default_model_replacement': False,
        'comparison_scope': 'Frozen model comparison, not a controlled data-only ablation. Training sizes, learning-rate schedule and PdM threshold selection differ.',
        'uncertainty_scope': 'Wilson intervals are window/image-level references. Paired nuisance seeds and shared geometry groups do not constitute independent physical machines or products.',
        'visibility_scope': 'Visible high-contrast surface proxies; low-contrast and real defects remain unvalidated.',
        'vision_stress_scope': 'Postprocessing of reused heldout images; not independent geometry, a new renderer or real-camera validation. Erase/inpaint creates artifacts and is not a causal proof.',
        'reports': {name: sha256(root / f'{name}-challenge.json') for name in reports},
        'models': {},
    }
    for name, report in reports.items():
        sensor = report['sensor']['models']
        summary['models'][name] = {
            'status': report['status'], 'hashes': report['models'],
            'sensor': {model: {'aggregate': result['aggregate'],
                              'fault_type_metrics': result['fault_type_metrics']}
                       for model, result in sensor.items()},
            'vision': report['vision']['summary'],
            'warnings': report['findings'],
        }
    write_report(root / 'comparison.json', summary)
    plot_comparison(root, reports)
    return summary


def plot_comparison(root, reports):
    colors = {'baseline': '#4169a1', 'candidate': '#00856b'}
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for name, report in reports.items():
        offset = -.18 if name == 'baseline' else .18
        x = np.arange(2) + offset
        values = [report['sensor']['models'][model]['aggregate']['supported'] for model in ('chart', 'ae')]
        for ax, metric in zip(axes[0], ('f1', 'normal_fpr')):
            bars = ax.bar(x, [value[metric] for value in values], width=.34,
                          color=colors[name], label=name)
            ax.bar_label(bars, fmt='%.3f', fontsize=9, padding=3)
            ax.set_xticks([0, 1], ['Chart', 'Autoencoder'])
            ax.set_ylim(0, 1.08)
        vision = report['vision']['summary']
        transforms = ('identity', 'grayscale', 'darken', 'brighten', 'blur', 'jpeg')
        axes[1, 0].plot(transforms, [vision[t]['recall_iou50'] for t in transforms],
                        marker='o', color=colors[name], label=name)
        axes[1, 1].plot(transforms + ('erase_defect',),
                        [vision[t]['normal_image_fpr'] for t in transforms + ('erase_defect',)],
                        marker='o', color=colors[name], label=name)
    axes[0, 0].set_title('Independent sensor challenge: supported F1')
    axes[0, 0].axhline(.8, color='#8a3434', ls='--', lw=1, label='registered minimum')
    axes[0, 1].set_title('Independent sensor challenge: normal false-positive rate')
    axes[0, 1].axhline(.05, color='#8a3434', ls='--', lw=1, label='registered maximum')
    axes[1, 0].set_title('Paired vision stress: class + IoU50 recall (120 positives)')
    axes[1, 1].set_title('Vision false-positive rate (40 normal / 120 erased images)')
    for ax in axes.flat:
        ax.grid(axis='y', alpha=.2)
        ax.set_axisbelow(True)
        ax.legend(fontsize=8, loc='best')
    for ax in axes[1]:
        ax.set_ylim(0, 1.05)
        ax.tick_params(axis='x', rotation=30, labelsize=8)
    fig.suptitle('Same frozen protocol and inputs | Synthetic checks; real factory UNVALIDATED', fontsize=12)
    fig.savefig(root / 'challenge-comparison.png', dpi=150)
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.results)
    print(json.dumps({name: row['status'] for name, row in result['models'].items()}))
