"""Plot recorded simulator/PdM evidence and publish measured metrics, never target values."""
import json
import shutil
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from factory.common import DATA, ARTIFACTS, CONFIG
from factory.pdm.features import extract
from factory.pdm.model import Detector
from factory.pdm.train import benchmark_rotation


def render():
    """Create scientific figures from actual windows and the untouched test split."""
    output=Path(CONFIG['paths']['reports'])
    dataset=np.load(DATA/'sensor/dataset.npz')
    rotation=benchmark_rotation(dataset)
    figure,axes=plt.subplots(2,2,figsize=(10,6),constrained_layout=True)
    for row,name in enumerate(['normal','fault']):
        wave=dataset[f'{name}_wave']
        axes[row,0].plot(np.arange(len(wave))/2048,wave,lw=.6,color=['#17856b','#db5061'][row])
        axes[row,0].set(title=f'{name.title()} vibration',xlabel='Time (s)',ylabel='Acceleration (a.u.)')
        _,spectrum=extract(wave,rotation_hz=rotation)
        axes[row,1].plot(spectrum['frequencies'],spectrum['amplitudes'],color=['#17856b','#db5061'][row],lw=1)
        axes[row,1].axvline(spectrum['bpfo_hz'],ls='--',color='#666',label='BPFO')
        axes[row,1].axvline(spectrum['bpfi_hz'],ls=':',color='#999',label='BPFI')
        axes[row,1].set(title='Hann FFT / 1 Hz bins',xlabel='Frequency (Hz)',ylabel='Amplitude')
        axes[row,1].legend()
    figure.savefig(output/'vibration_fft.png',dpi=150)
    plt.close(figure)
    detector=Detector(ARTIFACTS/'pdm.pt')
    selected=dataset['splits']=='test'
    chart,ae=detector.errors(dataset['features'][selected])
    levels=dataset['levels'][selected]
    figure,axes=plt.subplots(1,2,figsize=(10,3.5),constrained_layout=True)
    for axis,name,values in zip(axes,['chart','ae'],[chart,ae]):
        axis.scatter(levels,values,s=8,alpha=.35)
        axis.axhline(detector.thresholds[name],ls='--',color='red',label='Validation threshold')
        axis.set(title=name.upper()+' / held-out test',xlabel='Injected fault level (evaluation label)',ylabel='Measured anomaly error')
        axis.legend()
    figure.savefig(output/'pdm_test_errors.png',dpi=150)
    plt.close(figure)
    csv=DATA/'training/vision/results.csv'
    if csv.exists():
        shutil.copy2(csv,output/'vision-training.csv')
    chart_file=DATA/'training/vision/results.png'
    if chart_file.exists():
        shutil.copy2(chart_file,output/'vision-training.png')
    elif csv.exists():
        history=np.genfromtxt(csv,delimiter=',',names=True)
        figure,axis=plt.subplots(figsize=(7,3.5),constrained_layout=True)
        axis.plot(history['epoch'],history['metricsmAP50B'],label='Validation mAP50')
        axis.plot(history['epoch'],history['metricsmAP5095B'],label='Validation mAP50–95')
        axis.set(xlabel='Completed epoch',ylabel='Validation AP',ylim=(0,1),title='YOLOv8n training (validation only)')
        axis.legend()
        figure.savefig(output/'vision-training.png',dpi=150)
        plt.close(figure)
    print(f'Actual experiment figures saved to {output}')


if __name__=='__main__':
    render()
