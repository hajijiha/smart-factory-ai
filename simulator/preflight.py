"""Capture 16 genuine Gazebo frames for geometry review before bulk acquisition.

These images are diagnostics outside DATA_DIR, not members of any training split.
The source configuration is kept intact; no reduced-count dataset is substituted.
"""
import json
from pathlib import Path
import cv2
import numpy as np
from factory.common import CONFIG, utc_now
from simulator.dataset_protocol import specification, verify_plugin_binding, digest, write_json
from simulator.scenarios import groups, vision_scenes
from simulator.geometry import project_bbox


def preflight_scenes(configuration):
    """Use all split families and varied scales, without looking at model results."""
    families = list(groups('vision', configuration))
    selected = [next(group for group in families if group['split'] == split) for split in ('train','val','test')]
    selected.append(next(group for group in families if group['split']=='train' and group['group_id']!=selected[0]['group_id']))
    for group_number, group in enumerate(selected):
        candidates = list(vision_scenes(group, configuration))
        for class_id in (-1,0,1,2):
            choices = [scene for scene in candidates if scene['class_id']==class_id]
            choices.sort(key=lambda scene:scene['defect_scale'])
            rank = {0:0.,1:.5,2:1.,-1:.5}[class_id]
            if group_number % 2:
                rank = 1-rank
            scene = dict(choices[round(rank*(len(choices)-1))])
            scene['scene_id'] = 'preflight-' + scene['scene_id']
            yield scene


def capture_preflight(node, destination):
    identity = specification(CONFIG, verify_plugin_binding())
    root = Path(destination)
    if root.exists() and any(root.iterdir()):
        raise RuntimeError('Preflight output already exists; preserve it and choose a new PREFLIGHT_DIR')
    root.mkdir(parents=True,exist_ok=True)
    records=[]
    for number, scene in enumerate(preflight_scenes(CONFIG)):
        label=project_bbox(scene)
        image,joint,sim_time=node.capture(scene)
        rng=np.random.default_rng(scene['seed']+int(scene['scene_id'].rsplit('-',1)[1])*43)
        image=np.clip(image.astype(np.float32)+rng.normal(0,scene['image_noise'],image.shape),0,255).astype(np.uint8)
        image_path=root/f'{number:02d}_{scene["split"]}_{scene["class_id"]}.jpg'
        label_path=image_path.with_suffix('.txt')
        if not cv2.imwrite(str(image_path),image):
            raise RuntimeError('Could not save Gazebo preflight image')
        label_path.write_text('' if label is None else ' '.join(map(str,label))+'\n')
        records.append({'image':image_path.name,'label':label_path.name,'scene':scene,'joint':joint,
            'camera_sim_time':sim_time,'timestamp':utc_now(),'sha256':digest(image_path),'label_sha256':digest(label_path),
            'generator_version':'v2','fingerprint':identity['fingerprint'], 'capture_index':number})
    manifest=root/'manifest.jsonl'
    manifest.write_text('\n'.join(json.dumps(row,sort_keys=True,allow_nan=False) for row in records)+'\n')
    write_json(root/'preflight.json',{**identity,'capture_count':len(records),'manifest_sha256':digest(manifest),
        'purpose':'Rendering geometry gate only; not a dataset and not used for model fitting or threshold selection',
        'visual_review_status':'pending external visual inspection',
        'expected_scale_semantics':'Unit box dimensions [x,y,z]; unit cylinder dimensions [2radius,2radius,length]'})
    print(f'Preflight captured {len(records)} real Gazebo frames at {root}',flush=True)
