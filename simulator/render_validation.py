"""Validate labels against actual paired Gazebo pixels before image acquisition."""
import json
from pathlib import Path
import cv2
import numpy as np
from factory.common import CONFIG, utc_now
from simulator.dataset_protocol import specification, verify_plugin_binding, digest, write_json
from simulator.scenarios import groups, vision_scenes, defect_primitives, apply_visibility_policy
from simulator.geometry import project_bbox

CRITERIA = {'minimum_bbox_iou': .70, 'maximum_outside_roi_fraction': .10,
    'rounding_tolerance_pixels': 2, 'changed_pixel_channel_threshold': 20, 'minimum_changed_pixels': 5}
SCALES = (.68, .93, 1.18)
CAPTURE_POLICY = {'minimum_wall_seconds_after_ack': .25, 'minimum_fresh_frames': 3,
    'minimum_sim_seconds_after_ack': .10}
SCENE_APPLICATION_POLICY = {'application_rounds': 3,
    'minimum_seconds_between_ack_and_next_command': .10}


class SceneApplication:
    """Reapply the same desired scene on a fixed schedule, independently of pixels.

    Acknowledgments prove application on the physics thread and enqueueing of
    visual messages, rather than completion in the renderer. Actual render/label
    consistency remains a separate paired-image validation gate.
    """
    def __init__(self, scene_id, token):
        self.scene_id = scene_id
        self.application_id = f'{scene_id}:{token}'
        self.round = 0
        self.command_wall = None
        self.records = []

    def fields(self):
        return {'scene_application_id': self.application_id, 'scene_apply_round': self.round,
            'scene_command_id': f'{self.application_id}:{self.round}'}

    def matches(self, ack):
        return bool(ack) and ack.get('scene_id')==self.scene_id and all(ack.get(k)==v for k,v in self.fields().items())

    @property
    def acknowledged(self):
        return len(self.records)>self.round

    @property
    def complete(self):
        return len(self.records)==SCENE_APPLICATION_POLICY['application_rounds']

    def sent(self, now):
        if self.command_wall is None:
            self.command_wall = now

    def acknowledge(self, ack, wall, sequence):
        if not self.matches(ack) or self.acknowledged or self.command_wall is None:
            return False
        if ack.get('ack_scope')!='physics_applied_and_visual_messages_enqueued':
            raise RuntimeError('v2 scene acknowledgment lacks explicit application scope')
        self.records.append({'scene_id':self.scene_id, **self.fields(), 'command_monotonic':self.command_wall,
            'ack_arrival_monotonic':wall, 'ack_sim_time':ack['sim_time'], 'ack_image_sequence':sequence,
            'ack_scope':ack['ack_scope'], 'visual_outgoing_count':ack.get('visual_outgoing_count')})
        return True

    def advance(self, now):
        if not self.acknowledged or self.complete:
            return False
        if now < self.records[-1]['ack_arrival_monotonic']+SCENE_APPLICATION_POLICY['minimum_seconds_between_ack_and_next_command']:
            return False
        self.round += 1
        self.command_wall = None
        return True


class CaptureFreshness:
    """Wait for transport/render updates without selecting frames by their pixels.

    Simulation time can advance faster than the rendering transport. Therefore
    a new camera stamp alone cannot establish that every visual update reached
    the renderer. The first frame arriving after a wall-clock barrier starts a
    three-frame freshness interval; frames seen before that barrier do not count.
    """
    def __init__(self, ack_wall, ack_sim_time):
        self.ack_wall = ack_wall
        self.ack_sim_time = ack_sim_time
        self.first_sequence = None
        self.fresh_frame_sequences = []
        self.fresh_frame_sim_times = []
        self.observation = None

    def ready(self, now, sequence, image_wall, image_sim_time, joint_sim_time):
        barrier = self.ack_wall + CAPTURE_POLICY['minimum_wall_seconds_after_ack']
        if now < barrier or image_wall < barrier:
            return False
        sim_barrier = self.ack_sim_time + CAPTURE_POLICY['minimum_sim_seconds_after_ack']
        if image_sim_time < sim_barrier:
            return False
        if self.fresh_frame_sequences and (sequence <= self.fresh_frame_sequences[-1] or image_sim_time <= self.fresh_frame_sim_times[-1]):
            return False
        if self.first_sequence is None:
            self.first_sequence = sequence
        self.fresh_frame_sequences.append(sequence)
        self.fresh_frame_sim_times.append(image_sim_time)
        fresh_frames = len(self.fresh_frame_sequences)
        if fresh_frames < CAPTURE_POLICY['minimum_fresh_frames'] or joint_sim_time < sim_barrier:
            return False
        self.observation = {'policy': dict(CAPTURE_POLICY), 'ack_arrival_monotonic': self.ack_wall,
            'ack_sim_time': self.ack_sim_time, 'first_fresh_sequence': self.first_sequence,
            'image_sequence': sequence, 'fresh_frames': fresh_frames,
            'fresh_frame_sequences': list(self.fresh_frame_sequences),
            'fresh_frame_sim_times': list(self.fresh_frame_sim_times),
            'image_arrival_monotonic': image_wall, 'capture_monotonic': now,
            'settling_wall_seconds': image_wall - self.ack_wall,
            'camera_sim_time': image_sim_time, 'joint_sim_time': joint_sim_time}
        return True


def iou(a, b):
    a, b = np.asarray(a), np.asarray(b)
    intersection = np.maximum(0, np.minimum(a[2:],b[2:])-np.maximum(a[:2],b[:2])).prod()
    union = np.maximum(0,a[2:]-a[:2]).prod()+np.maximum(0,b[2:]-b[:2]).prod()-intersection
    return float(intersection/union) if union else 0.


def compare_pixels(before, after, expected):
    """Use raw render differences; no detector, segmentation model or image noise.

    Each observed edge within two pixels of its analytical edge is snapped to
    that edge for raster rounding. Raw IoU is reported separately so this
    tolerance is explicit and cannot mask a large rendering scale error.
    """
    if before.shape != after.shape:
        raise ValueError('Paired camera dimensions differ')
    changed=np.max(np.abs(after.astype(np.int16)-before.astype(np.int16)),axis=2)>CRITERIA['changed_pixel_channel_threshold']
    yy,xx=np.where(changed)
    count=len(xx)
    if not count:
        return {'status':'FAIL','changed_pixels':0,'outside_roi_pixels':0,'bbox_iou':0.,'raw_bbox_iou':0.,'outside_roi_fraction':1.,'observed_bbox_pixels':None}
    observed=np.array([xx.min(),yy.min(),xx.max()+1,yy.max()+1],dtype=float)
    expected=np.asarray(expected,dtype=float)
    tolerance=CRITERIA['rounding_tolerance_pixels']
    snapped=np.where(np.abs(observed-expected)<=tolerance,expected,observed)
    roi_inside=(xx>=expected[0]-tolerance)&(xx<expected[2]+tolerance)&(yy>=expected[1]-tolerance)&(yy<expected[3]+tolerance)
    outside=float(np.mean(~roi_inside))
    metric=iou(expected,snapped)
    passed=count>=CRITERIA['minimum_changed_pixels'] and metric>=CRITERIA['minimum_bbox_iou'] and outside<=CRITERIA['maximum_outside_roi_fraction']
    return {'status':'PASS' if passed else 'FAIL','changed_pixels':count,'outside_roi_pixels':int((~roi_inside).sum()),'bbox_iou':metric,
        'raw_bbox_iou':iou(expected,observed),'outside_roi_fraction':outside,'observed_bbox_pixels':observed.tolist()}


def paired_scenes(configuration):
    families=list(groups('vision',configuration))
    chosen=[next(group for group in families if group['parameters']['material_id']==material) for material in range(3)]
    for class_id in range(3):
        for scale_index,scale in enumerate(SCALES):
            group=chosen[(class_id+scale_index)%3]
            base=next(scene for scene in vision_scenes(group,configuration) if scene['class_id']==-1)
            pair_id=f'class{class_id}-scale{scale:.2f}'
            normal={**base,'scene_id':f'render-check-{pair_id}-normal','class_id':-1,'defect_primitives':[]}
            defect={**base,'scene_id':f'render-check-{pair_id}-defect','class_id':class_id,
                'defect_scale':scale,'defect_darkness':.12,
                'defect_primitives':defect_primitives(class_id,group['seed'],scale,.12)}
            defect=apply_visibility_policy(defect,configuration)
            yield pair_id,normal,defect


def capture_render_pair(node, root, pair_id, normal, defect, directory, fingerprint):
    """Capture a fixed before/after pair once; preserve even failed raw frames."""
    root=Path(root)
    label=project_bbox(defect)
    before,before_joint,before_time=node.capture(normal)
    before_sync=dict(node.last_capture_info)
    after,after_joint,after_time=node.capture(defect)
    after_sync=dict(node.last_capture_info)
    height,width=after.shape[:2]
    _,cx,cy,w,h=label
    expected=[(cx-w/2)*width,(cy-h/2)*height,(cx+w/2)*width,(cy+h/2)*height]
    metrics=compare_pixels(before,after,expected)
    paths={kind:Path(directory)/f'{pair_id}-{kind}.png' for kind in ('normal','defect')}
    for kind,pixels in [('normal',before),('defect',after)]:
        (root/paths[kind]).parent.mkdir(parents=True,exist_ok=True)
        if (root/paths[kind]).exists() or not cv2.imwrite(str(root/paths[kind]),pixels):
            raise RuntimeError('Could not save a new raw Gazebo render validation frame')
    record={'pair_id':pair_id,'class_id':defect['class_id'],'scale':defect['defect_scale'],
            'normal_image':paths['normal'].as_posix(),'defect_image':paths['defect'].as_posix(),
            'normal_sha256':digest(root/paths['normal']),'defect_sha256':digest(root/paths['defect']),
            'normal_scene':normal,'defect_scene':defect,'expected_bbox_pixels':expected,
            'gt_bbox_xyxy':expected,'difference_bbox_xyxy':metrics['observed_bbox_pixels'],
            'normal_camera_sim_time':before_time,'defect_camera_sim_time':after_time,
            'normal_capture_sync':before_sync,'defect_capture_sync':after_sync,
            'normal_joint':before_joint,'defect_joint':after_joint,'metrics':metrics,
            'fingerprint':fingerprint,'model_used':False,'image_noise_applied':False,'criteria':dict(CRITERIA)}
    return record,before,after,after_joint,after_time


def check_render_pair(root, case, fingerprint):
    """Recompute every raw pair, including all bulk defect counterparts."""
    root=Path(root)
    if case.get('fingerprint')!=fingerprint or case.get('model_used') is not False or case.get('image_noise_applied') is not False or case.get('criteria')!=CRITERIA:
        raise RuntimeError('Invalid render pair identity or protocol')
    normal,defect=case['normal_scene'],case['defect_scene']
    if normal['class_id']!=-1 or normal['defect_primitives'] or defect['class_id']!=case['class_id'] or normal['scene_id']==defect['scene_id']:
        raise RuntimeError('Invalid normal/defect render pair scenes')
    for key in ('x','y','yaw','dx','dy','defect_yaw','camera_pose','product_color','background_color','light','material_id','specular','condition_id','group_id','visibility_policy'):
        if normal[key]!=defect[key]:
            raise RuntimeError(f'Render pair changed nuisance condition: {key}')
    before_path=root/case['normal_image']; after_path=root/case['defect_image']
    if digest(before_path)!=case['normal_sha256'] or digest(after_path)!=case['defect_sha256']:
        raise RuntimeError('Modified render consistency image')
    before,after=cv2.imread(str(before_path)),cv2.imread(str(after_path))
    _,cx,cy,w,h=project_bbox(defect)
    height,width=after.shape[:2]
    expected=[(cx-w/2)*width,(cy-h/2)*height,(cx+w/2)*width,(cy+h/2)*height]
    if not np.allclose(expected,case['expected_bbox_pixels'],rtol=0,atol=1e-9):
        raise RuntimeError('Render pair analytical label changed')
    fresh=compare_pixels(before,after,expected)
    if fresh!=case['metrics'] or fresh['status']!='PASS':
        raise RuntimeError('Render consistency pixel metrics failed revalidation')
    return fresh


def verify_rendering(node, destination):
    identity=specification(CONFIG,verify_plugin_binding())
    root=Path(destination)
    if root.exists() and any(root.iterdir()):
        raise RuntimeError('Render check output already exists; preserve it and choose a new output directory')
    (root/'render-check').mkdir(parents=True,exist_ok=True)
    cases=[]; panels=[]
    for pair_id,normal,defect in paired_scenes(CONFIG):
        record,before,after,_,_=capture_render_pair(node,root,pair_id,normal,defect,'render-check',identity['fingerprint'])
        cases.append(record)
        height,width=after.shape[:2]
        expected,metrics=record['expected_bbox_pixels'],record['metrics']
        panel=np.full((height+60,width*2+8,3),240,dtype=np.uint8)
        panel[50:50+height,:width]=before
        panel[50:50+height,width+8:]=after
        color=(0,150,0) if metrics['status']=='PASS' else (0,0,200)
        cv2.putText(panel,f'{pair_id} {metrics["status"]}',(8,18),cv2.FONT_HERSHEY_SIMPLEX,.48,color,1,cv2.LINE_AA)
        cv2.putText(panel,f'IoU {metrics["bbox_iou"]:.3f} raw {metrics["raw_bbox_iou"]:.3f} outside {metrics["outside_roi_fraction"]:.3f}',(8,40),cv2.FONT_HERSHEY_SIMPLEX,.42,color,1,cv2.LINE_AA)
        cv2.rectangle(panel,(width+8+int(expected[0]),50+int(expected[1])),(width+8+int(expected[2]),50+int(expected[3])),(0,255,0),1)
        if metrics['observed_bbox_pixels']:
            observed=metrics['observed_bbox_pixels']
            cv2.rectangle(panel,(width+8+int(observed[0]),50+int(observed[1])),(width+8+int(observed[2]),50+int(observed[3])),(0,0,255),1)
        panels.append(panel)
    montage=np.vstack([np.hstack(panels[index:index+3]) for index in (0,3,6)])
    cv2.imwrite(str(root/'render-check-montage.png'),montage)
    report={**identity,'schema_version':1,'generator_schema_version':2,
        'status':'PASS' if all(case['metrics']['status']=='PASS' for case in cases) else 'FAIL',
        'case_count':len(cases),'model_used':False,'criteria':CRITERIA,'cases':cases,
        'capture_policy':CAPTURE_POLICY,
        'scene_application_policy':SCENE_APPLICATION_POLICY,
        'created_at':utc_now(),'image_source':'Raw Gazebo camera frames, PNG without added sensor/image noise',
        'pair_policy':'Same camera, product/defect pose, light, materials and background; only defect geometry changes',
        'montage':'render-check-montage.png','montage_sha256':digest(root/'render-check-montage.png')}
    write_json(root/'render-check.json',report)
    if report['status']!='PASS':
        raise RuntimeError(f'Render/label consistency check FAILED; inspect {root}/render-check.json')
    return report


def verify_contrast_diagnostic(node, input_failure, destination):
    """Inspect a predetermined failed scene once under the new global policy.

    This is disclosure evidence, separate from the nine-case gate and datasets;
    no scene/seed selection, threshold change or recapture depends on its pixels.
    """
    original=json.loads(Path(input_failure).read_text())
    old_scene=original['render_pair']['defect_scene']
    scene=apply_visibility_policy(old_scene,CONFIG)
    identity=specification(CONFIG,verify_plugin_binding())
    root=Path(destination)
    if root.exists() and any(root.iterdir()):
        raise RuntimeError('Contrast diagnostic output exists; preserve it and use a new directory')
    root.mkdir(parents=True,exist_ok=True)
    normal={**scene,'scene_id':scene['scene_id']+'-visibility-diagnostic-normal','class_id':-1,'defect_primitives':[]}
    defect={**scene,'scene_id':scene['scene_id']+'-visibility-diagnostic-defect'}
    case,_,_,_,_=capture_render_pair(node,root,'fixed-failed-scene',normal,defect,'render-check',identity['fingerprint'])
    report={**identity,'schema_version':1,'generator_schema_version':2,'model_used':False,
        'diagnostic_type':'Predetermined r2 failed-scene inspection under a new global high-contrast material policy',
        'status':case['metrics']['status'],'case_count':1,'criteria':CRITERIA,'capture_policy':CAPTURE_POLICY,
        'scene_application_policy':SCENE_APPLICATION_POLICY,'case':case,'original_fingerprint':original['fingerprint'],
        'original_scene_id':old_scene['scene_id'],'original_scene':old_scene,'original_metrics':original['render_pair']['metrics'],
        'input_failure_sha256':digest(input_failure),'created_at':utc_now(),
        'scope':'Diagnostic only; cannot replace the independent nine-case gate or validate the failed r2 acquisition.'}
    write_json(root/'contrast-diagnostic.json',report)
    if report['status']!='PASS':
        raise RuntimeError('Fixed-scene contrast diagnostic failed; evidence preserved without retry')
    return report


def check_render_report(root, identity):
    """Reject stale, modified or incomplete rendering evidence before reuse."""
    root=Path(root); report=json.loads((root/'render-check.json').read_text())
    if report.get('fingerprint')!=identity['fingerprint'] or report.get('schema_version')!=1 or report.get('status')!='PASS' or report.get('model_used') is not False or report.get('criteria')!=CRITERIA or report.get('capture_policy')!=CAPTURE_POLICY or report.get('scene_application_policy')!=SCENE_APPLICATION_POLICY:
        raise RuntimeError('Invalid or stale render consistency report')
    cases=report.get('cases',[])
    matrix={(case['class_id'],case['scale']) for case in cases}
    if report.get('case_count')!=9 or len(cases)!=9 or matrix!={(c,s) for c in range(3) for s in SCALES}:
        raise RuntimeError('Incomplete render consistency case matrix')
    for case in cases:
        check_render_pair(root,case,identity['fingerprint'])
    return report
