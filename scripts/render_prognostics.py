"""Render saved held-out and online RUL observations without fitting or changing predictions."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from factory.settings import REPORTS


def render(directory):
    """Plot one held-out unit and the separately observed live Gazebo wear trajectory."""
    offline = json.loads((directory / 'rul.json').read_text())
    online = json.loads((directory / 'optional-runtime.json').read_text())
    unit = offline['predictions'][0]['unit_id']
    test = [row for row in offline['predictions'] if row['unit_id'] == unit]
    live = [row for row in online['observations'] if row['rul']['status'] == 'estimated']
    figure, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    axes[0].plot([row['age_seconds'] for row in test], [row['true_rul_seconds'] for row in test], label='True RUL')
    axes[0].plot([row['age_seconds'] for row in test], [row['predicted_rul_seconds'] for row in test], label='Frozen LSTM')
    axes[0].set_title(f'Held-out synthetic unit: {unit}')
    axes[1].plot([row['wear_scenario']['age_seconds'] for row in live], [row['wear_scenario']['true_rul_seconds'] for row in live], label='True RUL')
    axes[1].plot([row['wear_scenario']['age_seconds'] for row in live], [row['rul']['rul_seconds'] for row in live], label='Online MQTT / LSTM')
    axes[1].set_title(f'Live Gazebo wear: MAE {online["online_mae_seconds"]:.2f}s')
    for axis in axes:
        axis.set_xlabel('Virtual operating age (s)')
        axis.set_ylabel('Remaining virtual operating seconds')
        axis.grid(alpha=.2)
        axis.legend()
    figure.savefig(directory / 'rul-validation.png', dpi=160)
    plt.close(figure)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=REPORTS / 'completion-v3')
    args = parser.parse_args()
    render(args.directory)
