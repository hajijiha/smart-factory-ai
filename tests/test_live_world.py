"""Geometry checks that protect contact-driven transport and legacy dataset isolation."""
import math
import xml.etree.ElementTree as ET
import pytest
from factory.layout import get_layout, PRODUCT_SIZE
from factory.settings import CONFIG
from simulator.live_world import build_live
from simulator.world import build


def _world():
    """Parse the generated production SDF without running a physics substitute."""
    return ET.fromstring(build_live()).find('world')


def test_plates_rest_on_multiple_physical_rollers(monkeypatch):
    """Each dynamic plate has physical support, positive inertia and no initial overlap."""
    monkeypatch.setenv('FACTORY_POC_PRODUCTS', '10')
    world = _world()
    plates = [model for model in world.findall('model') if model.get('name').startswith('product_')]
    rollers = [model for model in world.findall('model') if model.get('name').startswith('roller_')]
    assert len(plates) == 10
    centers = []
    for plate in plates:
        pose = list(map(float, plate.findtext('pose').split()))
        centers.append(pose[0])
        assert plate.findtext('static') != 'true'
        assert float(plate.findtext('link/inertial/mass')) > 0
        assert all(float(plate.findtext('link/inertial/inertia/'+axis)) > 0 for axis in ('ixx', 'iyy', 'izz'))
        assert plate.find('link/collision') is not None
        supports = []
        for roller in rollers:
            roller_pose = list(map(float, roller.findtext('pose').split()))
            radius = float(roller.findtext('link[@name="shaft"]/collision/geometry/cylinder/radius'))
            if abs(roller_pose[0]-pose[0]) <= PRODUCT_SIZE[0]/2:
                supports.append(roller)
                assert 0 <= pose[2]-PRODUCT_SIZE[2]/2-(roller_pose[2]+radius) <= .01
        assert len(supports) >= 4
    assert all(b-a > PRODUCT_SIZE[0] for a, b in zip(sorted(centers), sorted(centers)[1:]))


def test_revolute_axis_drives_positive_x_and_records_mark(monkeypatch):
    """Roller cylinder axes are transverse and their positive angular velocity drives +x."""
    monkeypatch.setenv('FACTORY_POC_PRODUCTS', '3')
    world = _world()
    for roller in [model for model in world.findall('model') if model.get('name').startswith('roller_')]:
        rotation = list(map(float, roller.findtext('pose').split()))[3:]
        assert rotation[0] == pytest.approx(-math.pi/2)
        assert roller.findtext('joint[@name="shaft_joint"]/axis/xyz') == '0 0 1'
        assert roller.findtext('joint[@name="shaft_joint"]/parent') == 'world'
        assert len(roller.findall('link')) == 1
        assert roller.find('link[@name="shaft"]/visual[@name="rotation_mark"]') is not None
        assert float(roller.findtext('link[@name="shaft"]/collision/surface/friction/ode/mu')) > .1


@pytest.mark.parametrize('flag', ['GENERATE_DATA', 'GENERATE_RUL', 'GENERATE_CAUSAL'])
def test_generation_flags_override_inherited_live_mode(monkeypatch, flag):
    """Generation always keeps the old labeled world even when compose defaults to live."""
    for name in ('GENERATE_DATA', 'GENERATE_RUL', 'GENERATE_CAUSAL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('FACTORY_WORLD_MODE', 'live')
    monkeypatch.setenv(flag, '1')
    world = ET.fromstring(build()).find('world')
    assert world.find('model[@name="product"]') is not None
    assert world.find('model[@name="product_01"]') is None
    assert world.find('plugin/live_mode') is None


def test_live_cameras_and_common_layout_match(monkeypatch):
    """The production camera keeps the baseline optics and all shared IDs exist in SDF."""
    monkeypatch.setenv('FACTORY_POC_PRODUCTS', '10')
    world = _world()
    names = {model.get('name') for model in world.findall('model')}
    layout = get_layout()
    assert set(item['id'] for item in layout['objects']).issubset(names)
    inspection = world.find('model[@name="inspection_camera"]/link/sensor/camera')
    assert float(inspection.findtext('horizontal_fov')) == CONFIG['simulator']['camera_fov']
    assert int(inspection.findtext('image/width')) == CONFIG['simulator']['camera_size']
    assert len(world.findall('.//sensor[@type="camera"]')) == 2
    assert float(world.findtext('physics/real_time_update_rate')) > 0
    assert layout['transport_mode'] == 'physical_roller_friction'
    assert all('ground_truth_class' not in item for item in layout['objects'])


def test_sorting_has_wide_support_and_no_blocking_side_rails():
    """The pusher's two output lanes remain on rollers after leaving the narrow guide rails."""
    layout = get_layout()
    for item in layout['objects']:
        if item['id'].startswith('rail_'):
            assert item['position'][0]+item['size'][0]/2 <= .61
        if item['kind'] == 'roller' and item['position'][0] >= .75:
            assert item['size'][1]/2 > .55+PRODUCT_SIZE[1]/2


def test_roller_collisions_clear_all_static_supports_and_receiving_trays():
    """A wide transition roller must not intersect a fixed rail and defeat physical stopping."""
    layout = get_layout()
    rollers = [item for item in layout['objects'] if item['kind'] == 'roller']
    colliders = [item for item in layout['objects'] if item['kind'] in ('floor', 'frame', 'outfeed')]
    for roller in rollers:
        half = [roller['size'][0], roller['size'][1]/2, roller['size'][0]]
        for fixed in colliders:
            gap = [abs(a-b)-radius-size/2 for a, b, radius, size in
                   zip(roller['position'], fixed['position'], half, fixed['size'])]
            assert max(gap) >= .005, (roller['id'], fixed['id'], gap)
    assert layout['outfeed_policy']['scope'] == 'classification_and_handoff_on_outfeed_rollers'


def test_defect_reference_stays_private_and_balancing_is_explicit(monkeypatch):
    """Normal deployments use fault-driven sampling; balanced class generation requires opt-in."""
    monkeypatch.delenv('FACTORY_LIVE_DEFECT_MODE', raising=False)
    world = _world()
    assert world.findtext('plugin/defect_mode') == 'operational'
    assert float(world.findtext('plugin/initial_fault_level')) == CONFIG['simulator']['initial_fault_level']
    for visual in world.findall('model[@name="product_01"]/link/visual'):
        if visual.get('name').startswith('defect_'):
            assert float(visual.findtext('transparency')) == 1.
    assert 'class_id' not in str(get_layout())
    monkeypatch.setenv('FACTORY_LIVE_DEFECT_MODE', 'balanced-evaluation')
    assert _world().findtext('plugin/defect_mode') == 'balanced-evaluation'
    monkeypatch.setenv('FACTORY_LIVE_DEFECT_MODE', 'invented')
    with pytest.raises(ValueError, match='DEFECT_MODE'):
        build_live()


def test_quickstep_global_correction_limit_does_not_clip_motor_rows():
    """Gazebo's QuickStep applies its global cap to joint rows, so contact caps must be local."""
    world = _world()
    # The diagnostic shaft rotates at 1500 RPM (157.08 rad/s). A global .1 cap
    # falsely yielded .95 RPM despite visually advancing shaft angles at target.
    assert float(world.findtext('physics/ode/constraints/contact_max_correcting_vel')) >= 1500*2*math.pi/60
    contacts = world.findall('.//collision/surface/contact/ode/max_vel')
    assert contacts and all(0 < float(contact.text) <= .1 for contact in contacts)
    for joint in world.findall('.//joint[@name="shaft_joint"]'):
        assert float(joint.findtext('axis/limit/lower')) < -10000
        assert float(joint.findtext('axis/limit/upper')) > 10000


def test_selected_product_roi_uses_observed_pose_without_class_truth():
    """Product ownership excludes the real support-column false detection at the image edge."""
    from simulator.live_evaluation import product_roi
    plate = {'id': 'product_01', 'position': [0, 0, .58], 'quaternion': [0, 0, 0, 1]}
    box = product_roi(plate, 320, 320)
    assert 90 < box[0] < 100 and 220 < box[2] < 230
    assert box[0] < 160 < box[2] and box[1] < 160 < box[3]
    assert not box[0] <= 14 <= box[2]  # Observed support-column detection center.
    moved = product_roi({**plate, 'position': [-.08, 0, .58]}, 320, 320)
    assert moved[1] > box[1] and moved[3] > box[3]
