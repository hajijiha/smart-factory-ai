"""Opt-in acquisition of group-held-out, shuffled Gazebo rendering scenarios."""
import json
import logging
import os
import shutil
from collections import Counter
from pathlib import Path
import cv2
import numpy as np
from factory.common import CONFIG, DATA, utc_now
from simulator.dataset_protocol import specification, isolated_directory, cached, complete, digest, verify_plugin_binding, write_json
from simulator.scenarios import groups, vision_scenes
from simulator.geometry import project_bbox
from simulator.render_validation import verify_rendering, check_render_report, capture_render_pair, check_render_pair, CRITERIA


def generate_vision_v2(node):
    isolated_directory(DATA, CONFIG)
    root = DATA / 'vision'
    identity = specification(CONFIG, verify_plugin_binding())
    if cached(root, identity, ('manifest.jsonl', 'dataset.yaml', 'render-check.json')):
        check_render_report(root, identity)
        # Unlike v1, cached data verifies labels and every image, not marker existence alone.
        for line in (root / 'manifest.jsonl').read_text().splitlines():
            row = json.loads(line)
            if digest(root / row['image']) != row['sha256'] or digest(root / row['label']) != row['label_sha256']:
                raise RuntimeError(f"Modified v2 vision sample: {row['image']}")
            if row['scene']['class_id']>=0:
                check_render_pair(root,row['render_pair'],identity['fingerprint'])
        return
    root.mkdir(parents=True, exist_ok=True)
    external = os.environ.get('RENDER_CHECK_DIR')
    if external:
        gate_root = Path(external)
        gate = check_render_report(gate_root, identity)
        gate_files = ['render-check.json', gate['montage']]
        gate_files += [case[key] for case in gate['cases'] for key in ('normal_image', 'defect_image')]
        for name in gate_files:
            target=root/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(gate_root/name,target)
    else:
        # No full acquisition can complete without an actual rendering roundtrip.
        gate=verify_rendering(node,root/'gate-acquisition')
        gate_root=root/'gate-acquisition'
        gate_files=['render-check.json',gate['montage']]+[case[key] for case in gate['cases'] for key in ('normal_image','defect_image')]
        for name in gate_files:
            target=root/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(gate_root/name,target)
    check_render_report(root, identity)
    rows = []
    render_pair_count = 0
    counts = Counter()
    pending = root / 'manifest.pending.jsonl'
    with pending.open('w') as manifest:
        for group in groups('vision', CONFIG):
            for scene in vision_scenes(group, CONFIG):
                # Invalid visibility is a disclosed failed acquisition, never silently resampled.
                try:
                    label = project_bbox(scene)
                except ValueError as error:
                    failure = {'scene': scene, 'reason': str(error), 'fingerprint': identity['fingerprint']}
                    with (root / 'rejected.jsonl').open('a') as rejects:
                        rejects.write(json.dumps(failure) + '\n')
                    raise RuntimeError(f"v2 visibility rejection: {scene['scene_id']}: {error}") from error
                render_pair = None
                if scene['class_id']>=0:
                    normal={**scene,'scene_id':scene['scene_id']+'-counterpart-normal','class_id':-1,'defect_primitives':[]}
                    render_pair,_,image,joint,sim_time=capture_render_pair(node,root,scene['scene_id'],normal,scene,'render-pairs',identity['fingerprint'])
                    if render_pair['metrics']['status']!='PASS':
                        failure={'fingerprint':identity['fingerprint'],'capture_index':len(rows),
                            'reason':'Bulk render/label consistency failed; acquisition stopped without retry',
                            'render_pair':render_pair,'model_used':False}
                        write_json(root/'render-pair-failure.json',failure)
                        with (root/'rejected.jsonl').open('a') as rejects:
                            rejects.write(json.dumps(failure,sort_keys=True,allow_nan=False)+'\n')
                        raise RuntimeError(f"Bulk render/label consistency FAILED: {scene['scene_id']}; raw pair preserved")
                    render_pair_count += 1
                else:
                    image, joint, sim_time = node.capture(scene)
                rng = np.random.default_rng(scene['seed'] + int(scene['scene_id'].rsplit('-', 1)[1]) * 43)
                image = np.clip(image.astype(np.float32) + rng.normal(0, scene['image_noise'], image.shape), 0, 255).astype(np.uint8)
                split, class_id = scene['split'], scene['class_id']
                index = counts[split, class_id]
                image_path = root / 'images' / split / f'{class_id}_{index:05d}.jpg'
                label_path = root / 'labels' / split / f'{class_id}_{index:05d}.txt'
                image_path.parent.mkdir(parents=True, exist_ok=True)
                label_path.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(image_path), image):
                    raise RuntimeError(f'Could not save rendered image: {image_path}')
                label_path.write_text('' if label is None else ' '.join(map(str, label)) + '\n')
                row = {'image': str(image_path.relative_to(root)).replace('\\', '/'),
                    'label': str(label_path.relative_to(root)).replace('\\', '/'),
                    'scene': scene, 'joint': joint, 'camera_sim_time': sim_time, 'timestamp': utc_now(),
                    'capture_sync': dict(node.last_capture_info),
                    'render_pair':render_pair,
                    'generator_version': 'v2', 'fingerprint': identity['fingerprint'],
                    'capture_index': len(rows), 'sha256': digest(image_path), 'label_sha256': digest(label_path)}
                manifest.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
                rows.append(row)
                counts[split, class_id] += 1
                if len(rows) % 100 == 0:
                    manifest.flush()
                    logging.info('v2 Gazebo images: %d', len(rows))
    pending.replace(root / 'manifest.jsonl')
    (root / 'dataset.yaml').write_text(f'path: {root}\ntrain: images/train\nval: images/val\ntest: images/test\nnames: [scratch, dent, contamination]\n')
    complete(root, identity, ('manifest.jsonl', 'dataset.yaml', 'render-check.json'), len(rows),
        class_counts={f'{split}/{kind}': count for (split, kind), count in sorted(counts.items())},
        group_counts={split: len(set(row['scene']['group_id'] for row in rows if row['scene']['split'] == split))
            for split in ('train', 'val', 'test')},
        split_method='Independent geometry and rendering families; shuffled class acquisition inside each family',
        label_method='Projected boundary of the same Gazebo primitive definitions; no near-plane crossings or clipping',
        rejected_samples=0, realism='Synthetic surface visual proxies; no real-factory generalization claim',
        render_pair_count=render_pair_count,
        bulk_render_check={'status':'PASS','pair_count':render_pair_count,'method':'all_defect_normal_pairs',
            'model_used':False,'image_noise_applied':False,'criteria':dict(CRITERIA)},
        render_check={'path':'render-check.json','schema_version':1,'status':'PASS','case_count':9,'model_used':False})
