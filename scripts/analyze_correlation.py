"""Analyze and plot collected equipment/quality events without inventing observations."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from factory.common import CONFIG
from factory.analytics import associations


def analyze():
    """Use the runtime's identical 5-second bucket and lag algorithm."""
    directory=Path(CONFIG['paths']['reports'])
    observed=json.loads((directory/'correlation-observations.json').read_text())
    result=associations(observed['rows'])
    result.update(repeats=observed['repeats'],hold_seconds=observed['hold_seconds'],
                  steps=observed['steps'],source='actual Gazebo -> MQTT -> PostgreSQL joined event records')
    (directory/'correlation.json').write_text(json.dumps(result,indent=2))
    figure,axes=plt.subplots(1,2,figsize=(10,3.8),constrained_layout=True)
    buckets=result['buckets']
    axes[0].scatter([x['rms'] for x in buckets],[x['defect_rate'] for x in buckets],s=24,alpha=.7)
    pearson='undefined' if result['pearson'] is None else f"{result['pearson']:.3f}"
    spearman='undefined' if result['spearman'] is None else f"{result['spearman']:.3f}"
    axes[0].set(xlabel='Mean vibration RMS / 5 s',ylabel='Detected defect rate / inspected images',
                title=f'Pearson {pearson} / Spearman {spearman}')
    available=[x for x in result['lags'] if x['pearson'] is not None]
    axes[1].plot([x['seconds'] for x in available],[x['pearson'] for x in available],marker='o')
    axes[1].set(xlabel='Vibration leading quality (s)',ylabel='Pearson r',title='Observed lag sweep / serial dependence')
    figure.savefig(directory/'correlation.png',dpi=150)
    plt.close(figure)
    print(json.dumps({k:v for k,v in result.items() if k not in ['buckets','steps']},indent=2))


if __name__=='__main__':
    analyze()
