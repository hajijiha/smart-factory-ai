"""Generate virtual sensor windows from the live Gazebo joint baseline, atomically."""
import json
import logging
import time
import numpy as np
from factory.common import CONFIG, DATA, utc_now
from factory.pdm.features import extract
from simulator.signal import vibration


def generate_sensor(node):
    """Record 5,000 normal and 2,000 fault windows with separate scenario seeds."""
    path=DATA/'sensor/dataset.npz'
    if path.exists():
        return
    deadline=time.monotonic()+30
    with node.condition:
        while node.joint is None or abs(node.joint['velocity'][0])<1:
            if time.monotonic()>deadline:
                raise TimeoutError('Real Gazebo motor observation not available')
            node.condition.wait(.1)
    features,levels,splits,provenance=[],[],[],[]
    examples={}
    benchmark=None
    for split,ratio,seed in [('train',.70,401),('val',.15,502),('test',.15,603)]:
        rng=np.random.default_rng(seed)
        normal=int(CONFIG['training']['sensor_normal']*ratio)
        fault=int(CONFIG['training']['sensor_fault']*ratio)
        for index in range(normal+fault):
            level=0 if index<normal else int(rng.integers(2,11))
            with node.condition:
                joint=dict(node.joint)
            wave=vibration(level,joint['velocity'][0],joint['position'][0]+rng.uniform(0,6.28),rng,
                     noise={'train':.012,'val':.013,'test':.014}[split])
            vector,_=extract(wave,rotation_hz=abs(joint['velocity'][0])/(2*np.pi))
            features.append(vector); levels.append(level); splits.append(split)
            provenance.append({'joint':joint,'timestamp':utc_now(),'level':level,'split':split})
            benchmark=wave
            if level==0 and 'normal_wave' not in examples:
                examples['normal_wave']=wave
            if level==10 and 'fault_wave' not in examples:
                examples['fault_wave']=wave
            if index%250==0:
                logging.info('Gazebo sensor generation %s %d/%d',split,index,normal+fault)
                time.sleep(.01)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('dataset.pending.npz')
    np.savez_compressed(temporary,features=features,levels=levels,splits=splits,
                        benchmark_wave=benchmark,
                        benchmark_rotation_hz=abs(joint['velocity'][0])/(2*np.pi),**examples)
    temporary.replace(path)
    (path.parent/'provenance.jsonl').write_text('\n'.join(json.dumps(row) for row in provenance)+'\n')
    logging.info('Sensor dataset completed: %d records',len(levels))
