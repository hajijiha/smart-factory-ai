"""Generator contract tests: grouping, independent nuisances, geometry and cache safety."""
import copy
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
import cv2
import numpy as np
import pytest
from factory.common import CONFIG
from simulator.scenarios import groups, sensor_records, vision_scenes, generator_version, apply_visibility_policy
from simulator import scenarios
from simulator.dataset_protocol import specification, complete, cached, isolated_directory
from simulator.geometry import defect_points, project_bbox
from simulator.signal import vibration
from simulator import world
from simulator import dataset_protocol
from simulator.render_validation import compare_pixels, paired_scenes, CaptureFreshness, CAPTURE_POLICY, SceneApplication, capture_render_pair, check_render_pair


def test_opt_in_and_invalid_versions(monkeypatch):
    monkeypatch.delenv('DATA_GENERATOR_VERSION', raising=False)
    monkeypatch.delenv('GENERATOR_VERSION', raising=False)
    assert generator_version() == 'v1'
    monkeypatch.setenv('DATA_GENERATOR_VERSION', 'v2')
    assert generator_version() == 'v2'
    monkeypatch.setenv('DATA_GENERATOR_VERSION', 'future')
    with pytest.raises(ValueError):
        generator_version()


def test_sensor_group_counts_severity_coverage_and_fault_balance():
    counts, families, conditions, levels, types = Counter(), defaultdict(set), defaultdict(set), Counter(), Counter()
    schedule = list(groups('sensor', CONFIG))
    assert len(schedule) == 100
    for group in schedule:
        split = group['split']
        families[split].add(group['group_id'])
        conditions[split].add(group['condition_id'])
        records = sensor_records(group, CONFIG)
        assert Counter(level for level, _ in records)[0] == 50
        assert all(Counter(level for level, _ in records)[level] == 2 for level in range(1, 11))
        assert records != sorted(records), 'Labels must not be acquired in class/severity order'
        counts[split] += len(records)
        levels.update(level for level, _ in records)
        types.update((split, kind) for level, kind in records if level)
    assert counts == {'train':4900, 'val':1050, 'test':1050}
    assert levels[0] == 5000 and all(levels[level] == 200 for level in range(1, 11))
    for a, b in [('train','val'),('train','test'),('val','test')]:
        assert not families[a] & families[b]
        assert not conditions[a] & conditions[b]
    for split in counts:
        assert len(set(types[split, kind] for kind in ('imbalance','looseness','outer_race','inner_race','mixed'))) == 1
        conditions_for_split = [group['parameters'] for group in schedule if group['split']==split]
        assert {(p['rpm_bin'],p['noise_bin']) for p in conditions_for_split} == {(r,n) for r in range(3) for n in range(3)}


def test_quick_sensor_split_is_declared_and_supported():
    configuration = copy.deepcopy(CONFIG)
    configuration['data_generation']['sensor_groups'] = 20
    configuration['data_generation']['sensor_split_groups'] = [10, 5, 5]
    assert Counter(group['split'] for group in groups('sensor', configuration)) == {'train':10,'val':5,'test':5}
    assert all(8 <= group['parameters']['rotation_hz'] <= 15 for group in groups('sensor', configuration))
    configuration['data_generation']['sensor_rotation_hz'] = [10, 12]
    assert all(10 <= group['parameters']['rotation_hz'] <= 12 for group in groups('sensor', configuration))


def test_v2_noise_is_independent_of_severity_and_legacy_wave_is_unchanged():
    p = next(groups('sensor', CONFIG))['parameters']
    # Zero fault severity must not inject a noise difference due only to the type.
    a = vibration(0, 150, .4, np.random.default_rng(10), fault_type='normal', parameters=p)
    p2 = dict(p, noise=p['noise']*2)
    b = vibration(0, 150, .4, np.random.default_rng(10), fault_type='normal', parameters=p2)
    assert np.isfinite(a).all() and len(a) == 2048 and not np.array_equal(a, b)
    waves = [vibration(1,150,.4,np.random.default_rng(10),fault_type=kind,parameters=p)
        for kind in ('imbalance','looseness','outer_race','inner_race','mixed')]
    assert all(np.isfinite(w).all() for w in waves)
    assert len({w.tobytes() for w in waves}) == 5
    # Passing no v2 parameters preserves the original deterministic default path.
    assert np.array_equal(vibration(2,150,.4,np.random.default_rng(10)),
        vibration(2,150,.4,np.random.default_rng(10),parameters=None))


def test_vision_shuffle_group_balance_visibility_and_primitive_variation():
    counts, sizes, families = Counter(), defaultdict(set), defaultdict(set)
    for group in groups('vision', CONFIG):
        scenes = list(vision_scenes(group, CONFIG))
        classes = [scene['class_id'] for scene in scenes]
        assert Counter(classes) == {-1:30,0:10,1:10,2:10}
        assert classes != sorted(classes)
        # All classes share group material/background/lighting distribution.
        assert len({tuple(scene['product_color']) for scene in scenes}) == 1
        assert len({tuple(scene['background_color']) for scene in scenes}) == 1
        families[group['split']].add(scenes[0]['geometry_family'])
        for scene in scenes:
            counts[scene['split'],scene['class_id']] += 1
            label = project_bbox(scene)
            if scene['class_id'] < 0:
                assert label is None and not scene['defect_primitives']
            else:
                assert label[0] == scene['class_id'] and all(0 < value < 1 for value in label[1:])
                points = defect_points(scene['class_id'], scene['defect_primitives'])
                assert np.isfinite(points).all()
                sizes[scene['class_id']].add(tuple(np.ptp(points,axis=0).round(4)))
    assert sum(counts.values()) == 6000
    assert all(len(sizes[k]) > 100 for k in range(3))
    assert not families['train'] & families['test']
    assert not families['train'] & families['val']


def test_rendered_v2_slot_names_and_legacy_world(monkeypatch):
    monkeypatch.setenv('DATA_GENERATOR_VERSION','v2')
    document = ET.fromstring(world.build())
    for class_id, count in [(0,5),(1,2),(2,5)]:
        names = [v.get('name') for v in document.findall(f'./world/model[@name="defect_{class_id}"]/link/visual')]
        assert names == [f'part{i}' for i in range(count)]
    monkeypatch.setenv('DATA_GENERATOR_VERSION','v1')
    document = ET.fromstring(world.build())
    assert len(document.findall('./world/model[@name="defect_0"]/link/visual')) == 3
    assert document.find('./world/model[@name="defect_0"]/link/visual').get('name') == 'groove0'


def test_cache_fingerprint_integrity_and_baseline_write_protection(tmp_path, monkeypatch):
    identity = specification(CONFIG)
    root = tmp_path/'new-data'
    assert not cached(root, identity, ('dataset.npz',))
    root.mkdir()
    (root/'dataset.npz').write_bytes(b'example')
    with pytest.raises(RuntimeError,match='Partial'):
        cached(root,identity,('dataset.npz',))
    complete(root,identity,('dataset.npz',),1)
    assert cached(root,identity,('dataset.npz',))
    changed = dict(identity,fingerprint='changed')
    with pytest.raises(RuntimeError,match='Stale'):
        cached(root,changed,('dataset.npz',))
    (root/'dataset.npz').write_bytes(b'corrupt')
    with pytest.raises(RuntimeError,match='modified'):
        cached(root,identity,('dataset.npz',))
    monkeypatch.delenv('DATA_DIR',raising=False)
    with pytest.raises(RuntimeError,match='explicit'):
        isolated_directory(root,CONFIG)
    monkeypatch.setenv('DATA_DIR',str(root))
    isolated_directory(root,CONFIG)
    with pytest.raises(RuntimeError,match='baseline'):
        isolated_directory(CONFIG['paths']['data'],CONFIG)


def test_projection_refuses_invisible_defect():
    group = next(groups('vision',CONFIG))
    scene = next(scene for scene in vision_scenes(group,CONFIG) if scene['class_id'] >= 0)
    scene['camera_pose'] = [0,0,.2,0,np.pi/2,0]
    with pytest.raises(ValueError,match='near plane'):
        project_bbox(scene)


def test_runtime_plugin_binding_is_part_of_dataset_identity_and_stale_source_fails(tmp_path, monkeypatch):
    binding = {'schema_version':1, 'source_path':'simulator/plugin/world.cpp',
        'library_path':'simulator/libfactory_world.so', 'source_sha256':'original', 'library_sha256':'binary'}
    assert specification(CONFIG,binding)['fingerprint'] != specification(CONFIG)['fingerprint']
    source = tmp_path/'simulator/plugin/world.cpp'
    source.parent.mkdir(parents=True)
    source.write_text('different source')
    marker = tmp_path/'simulator/plugin-build.json'
    marker.write_text(json.dumps(binding))
    monkeypatch.setattr(dataset_protocol,'__file__',str(tmp_path/'simulator/dataset_protocol.py'))
    with pytest.raises(RuntimeError,match='Stale plugin source'):
        dataset_protocol.verify_plugin_binding(timeout=0)


def test_pixel_gate_detects_large_rendering_scale_errors_and_allows_raster_rounding():
    before=np.zeros((80,80,3),dtype=np.uint8)
    correct=before.copy(); correct[21:39,21:39]=180
    close=compare_pixels(before,correct,[20,20,40,40])
    assert close['status']=='PASS' and close['bbox_iou']==1
    oversized=before.copy(); oversized[5:65,5:65]=180
    mismatch=compare_pixels(before,oversized,[20,20,40,40])
    assert mismatch['status']=='FAIL' and mismatch['outside_roi_fraction']>.80
    assert compare_pixels(before,before,[20,20,40,40])['status']=='FAIL'


def test_render_validation_pairs_change_only_defect_geometry_and_cover_case_matrix():
    pairs=list(paired_scenes(CONFIG))
    assert len(pairs)==9
    assert {(after['class_id'],after['defect_scale']) for _,before,after in pairs}=={(c,s) for c in range(3) for s in (.68,.93,1.18)}
    for pair_id,before,after in pairs:
        assert before['class_id']==-1 and not before['defect_primitives']
        assert before['scene_id']!=after['scene_id']
        for key in ('x','y','yaw','dx','dy','defect_yaw','camera_pose','product_color','background_color','light','material_id','specular','visibility_policy'):
            assert before[key]==after[key]
        assert project_bbox(after) is not None


def test_capture_freshness_rejects_pre_barrier_frames_and_counts_three_new_frames():
    gate=CaptureFreshness(10.,100.)
    # Large simulation progress and many old frames cannot bypass wall settling.
    assert not gate.ready(10.24,500,10.24,105.,105.)
    assert not gate.ready(10.30,500,10.24,105.,105.)
    assert not gate.ready(10.30,501,10.29,105.,105.)
    assert not gate.ready(10.35,501,10.29,105.,105.)  # Repeated same buffer is one frame.
    assert not gate.ready(10.36,502,10.35,105.1,105.)
    assert gate.ready(10.40,503,10.39,105.2,105.)
    assert gate.observation['fresh_frames']==3
    assert gate.observation['fresh_frame_sequences']==[501,502,503]
    assert gate.observation['settling_wall_seconds']>=CAPTURE_POLICY['minimum_wall_seconds_after_ack']


def test_capture_freshness_also_requires_post_ack_camera_and_joint_stamps():
    gate=CaptureFreshness(5.,50.)
    assert not gate.ready(5.30,10,5.29,49.,55.)
    assert not gate.ready(5.35,11,5.34,55.,49.)
    assert not gate.ready(5.40,12,5.39,55.1,49.)
    assert gate.ready(5.45,13,5.44,55.2,55.)


def test_capture_freshness_does_not_count_duplicate_camera_timestamps():
    gate=CaptureFreshness(5.,50.)
    assert not gate.ready(5.30,10,5.29,55.,55.)
    assert not gate.ready(5.35,11,5.34,55.,55.)
    assert not gate.ready(5.40,12,5.39,55.,55.)
    assert not gate.ready(5.45,13,5.44,55.1,55.)
    assert gate.ready(5.50,14,5.49,55.2,55.)
    assert gate.observation['fresh_frame_sequences']==[10,13,14]


def test_scene_application_requires_three_distinct_acknowledged_rounds_on_fixed_schedule():
    replay=SceneApplication('scene-a','transaction-1')
    for round_number in range(3):
        now=10.+round_number*.15
        replay.sent(now)
        ack={'scene_id':'scene-a',**replay.fields(),'sim_time':100.+round_number,
            'ack_scope':'physics_applied_and_visual_messages_enqueued','visual_outgoing_count':0}
        wrong=dict(ack,scene_command_id='stale-command')
        assert not replay.acknowledge(wrong,now+.01,50+round_number)
        assert replay.acknowledge(ack,now+.01,50+round_number)
        assert not replay.acknowledge(ack,now+.02,50+round_number)
        if round_number<2:
            assert not replay.complete
            assert not replay.advance(now+.05)
            assert replay.advance(now+.12)
        else:
            assert replay.complete and not replay.advance(now+5.)
    assert [r['scene_apply_round'] for r in replay.records]==[0,1,2]
    assert len({r['scene_command_id'] for r in replay.records})==3
    assert len({r['scene_application_id'] for r in replay.records})==1
    assert all(replay.records[i+1]['command_monotonic']>=replay.records[i]['ack_arrival_monotonic']+.10 for i in range(2))


def test_scene_application_rejects_ack_that_does_not_disclose_physics_scope():
    replay=SceneApplication('scene-a','transaction-1')
    replay.sent(10.)
    ack={'scene_id':'scene-a',**replay.fields(),'sim_time':100.}
    with pytest.raises(RuntimeError,match='application scope'):
        replay.acknowledge(ack,10.01,50)


def test_raw_render_pair_is_lossless_bound_to_scenes_and_modified_pixels_are_rejected(tmp_path):
    _,normal,defect=next(paired_scenes(CONFIG))
    label=project_bbox(defect)
    _,cx,cy,w,h=label
    size=CONFIG['simulator']['camera_size']
    expected=[(cx-w/2)*size,(cy-h/2)*size,(cx+w/2)*size,(cy+h/2)*size]
    before=np.zeros((size,size,3),dtype=np.uint8)
    after=before.copy()
    x0,y0,x1,y1=np.rint(expected).astype(int)
    after[y0:y1,x0:x1]=180
    class Camera:
        def capture(self,scene):
            self.last_capture_info={'scene_id':scene['scene_id']}
            return (before if scene['class_id']<0 else after).copy(),{'sim_time':1.},1.
    record,_,captured,_,_=capture_render_pair(Camera(),tmp_path,'fixed-pair',normal,defect,'render-pairs','test-fingerprint')
    assert record['metrics']['status']=='PASS'
    assert np.array_equal(cv2.imread(str(tmp_path/record['defect_image'])),captured)
    assert record['model_used'] is False and record['image_noise_applied'] is False
    assert check_render_pair(tmp_path,record,'test-fingerprint')['status']=='PASS'
    (tmp_path/record['normal_image']).write_bytes(b'corrupt')
    with pytest.raises(RuntimeError,match='Modified'):
        check_render_pair(tmp_path,record,'test-fingerprint')


def test_shared_visibility_policy_applies_to_every_class_and_preserves_raw_colors():
    seen=set()
    for group in groups('vision',CONFIG):
        for scene in vision_scenes(group,CONFIG):
            policy=scene['visibility_policy']
            assert policy['name']=='visible_surface_proxy_v1'
            assert .65<=policy['color_scale']<=1.
            assert policy['minimum_material_rgb_gap']==.30
            for part in scene['defect_primitives']:
                seen.add(scene['class_id'])
                assert 'color_before_contrast' in part
                assert all(0<=v<=policy['primitive_color_upper_bound'] for v in part['color'])
                assert np.all(np.asarray(scene['product_color'])-part['color']>=.30-1e-12)
        if seen=={0,1,2}:
            break
    assert seen=={0,1,2}


def test_visibility_mapping_does_not_change_nuisance_schedule_geometry_or_labels(monkeypatch):
    group=next(groups('vision',CONFIG))
    monkeypatch.setattr(scenarios,'apply_visibility_policy',lambda scene,configuration:scene)
    previous=list(vision_scenes(group,CONFIG))
    monkeypatch.setattr(scenarios,'apply_visibility_policy',apply_visibility_policy)
    current=list(vision_scenes(group,CONFIG))
    for old,new in zip(previous,current):
        for key,value in old.items():
            if key!='defect_primitives':
                assert new[key]==value
        assert project_bbox(old)==project_bbox(new)
        for before,after in zip(old['defect_primitives'],new['defect_primitives']):
            assert after['color_before_contrast']==before['color']
            assert all(after[key]==value for key,value in before.items() if key!='color')


def test_visibility_mapping_is_label_independent_idempotent_and_rejects_unsupported_material():
    scene=next(vision_scenes(next(groups('vision',CONFIG)),CONFIG))
    normal={**scene,'class_id':-1,'defect_primitives':[]}
    mapped=apply_visibility_policy(scene,CONFIG)
    assert mapped==apply_visibility_policy(mapped,CONFIG)
    assert mapped['visibility_policy']==apply_visibility_policy(normal,CONFIG)['visibility_policy']
    outside={**scene,'product_color':[.25,.25,.25]}
    with pytest.raises(ValueError,match='outside the declared'):
        apply_visibility_policy(outside,CONFIG)


def test_visibility_policy_rejects_unknown_policy_without_resampling():
    scene=next(vision_scenes(next(groups('vision',CONFIG)),CONFIG))
    configuration=copy.deepcopy(CONFIG)
    configuration['data_generation']['vision_visibility_policy']='unknown'
    with pytest.raises(ValueError,match='Unsupported'):
        apply_visibility_policy(scene,configuration)
