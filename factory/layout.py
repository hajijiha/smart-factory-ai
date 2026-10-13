"""One versioned, z-up geometry contract for Gazebo and the observed web viewer."""
import math
import os

WORLD_VERSION = 'live-v1'
ROLLER_RADIUS = .06
ROLLER_PITCH = .20
ROLLER_COUNT = 72
PRODUCT_SIZE = [.9, .7, .06]
PRODUCT_Z = .58
INFEED_X = -10.8
INSPECTION_X = 0.
OUTFEED_X = 2.0


def product_count():
    """Choose a three-product physical proof or the ten-product production scene."""
    count = int(os.environ.get('FACTORY_POC_PRODUCTS', '10'))
    if count not in (2, 3, 8, 9, 10, 11, 12):
        raise ValueError('FACTORY_POC_PRODUCTS must be 2, 3, or 8–12')
    return count


def _object(name, kind, size, position, color, shape='box', rotation=None):
    """Describe a primitive without leaking product defect labels into the viewer."""
    return {'id': name, 'kind': kind, 'shape': shape, 'size': list(size),
            'position': list(position), 'rotation': rotation or [0., 0., 0.], 'color': color}


def get_layout():
    """Return the complete layout in metres, radians, and Gazebo's z-up convention."""
    from factory.settings import CONFIG
    items = [_object('floor', 'floor', [19, 10, .10], [-4.6, 0, -.05], '#343c49'),
             _object('walkway', 'marking', [16, 1.15, .003], [-4.6, -2.3, .004], '#247a81'),
             _object('conveyor', 'conveyor', [14.8, 1.1, .10], [-4.6, 0, .38], '#243642')]
    for side in (-1, 1):
        items.append(_object(f'rail_{side}', 'frame', [12.3, .07, .14], [-5.55, side*.59, .49], '#557585'))
        for index in range(8):
            items.append(_object(f'leg_{side}_{index}', 'frame', [.10, .10, .4], [-11.4+index*2.0, side*.49, .2], '#445563'))
    for index in range(ROLLER_COUNT):
        x = -11.7+index*ROLLER_PITCH
        length = 2.35 if x >= .7 else 1.06
        items.append(_object(f'roller_{index:02d}', 'roller', [ROLLER_RADIUS, length],
                             [x, 0, .49], '#93a9b5', 'cylinder', [-math.pi/2, 0, 0]))
    items.extend([
        _object('drive_motor', 'drive_motor', [.7, .34, .35], [-10.3, -.87, .45], '#299b97'),
        _object('motor', 'motor', [.12, .25], [-2.0, -1.65, .65], '#f0aa55', 'cylinder', [0, math.pi/2, 0]),
        _object('motor_stand', 'frame', [1.05, .75, .46], [-2.0, -1.65, .23], '#394c5c'),
        _object('camera', 'camera', [.26, .20, .16], [0, 0, 2.8], '#63d7d0'),
        _object('camera_support', 'inspection_cell', [.10, .10, 2.8], [0, .91, 1.4], '#d6b35e'),
        _object('camera_boom', 'inspection_cell', [.10, 1.2, .10], [0, .36, 2.75], '#d6b35e'),
        _object('cell_left', 'inspection_cell', [.08, .08, 1.9], [-.7, .78, .95], '#d6b35e'),
        _object('cell_right', 'inspection_cell', [.08, .08, 1.9], [.7, .78, .95], '#d6b35e'),
        _object('cell_roof', 'inspection_cell', [1.6, .1, .1], [0, .78, 1.9], '#d6b35e'),
        _object('control_cabinet', 'cabinet', [.55, .38, 1.25], [-4.0, -1.6, .625], '#6e8796'),
        _object('control_screen', 'screen', [.36, .025, .28], [-4.0, -1.80, .88], '#52cfc0'),
        _object('beacon', 'beacon', [.09, .22], [-4.0, -1.6, 1.39], '#44cf91', 'cylinder'),
        _object('outfeed_base', 'frame', [2.3, 2.6, .10], [1.45, 0, .36], '#243642'),
        _object('sorter', 'sorter', [.25, .22, .23], [.9, 1.31, .55], '#d48b48'),
        _object('normal_outfeed', 'outfeed', [1.5, .90, .015], [3.35, -.6, .52], '#376c5c'),
        _object('reject_outfeed', 'outfeed', [1.5, .90, .015], [3.35, .6, .52], '#76514f')])
    for index in range(9):
        x = -12.0+index*2.0
        items.append(_object(f'fence_post_{index}', 'fence', [.07, .07, 1.3], [x, 2.5, .65], '#d0b45e'))
        if index < 8:
            items.append(_object(f'fence_panel_{index}', 'fence', [1.9, .035, 1.0], [x+1, 2.5, .70], '#64747c'))
    for index in range(3):
        x = -10+index*2.1
        items.append(_object(f'rack_{index}', 'rack', [1.6, .75, 1.7], [x, 3.4, .85], '#6c7d8f'))
        for shelf in range(3):
            items.append(_object(f'rack_box_{index}_{shelf}', 'stock', [1.3, .62, .24], [x, 3.4, .2+shelf*.55], '#b79b72'))
    for index in range(product_count()):
        items.append(_object(f'product_{index+1:02d}', 'product', PRODUCT_SIZE,
                             [INFEED_X+index*.98, 0, PRODUCT_Z+.005], '#bfc2c5'))
    return {'world_version': WORLD_VERSION, 'coordinate_system': 'z-up',
            'physics': {'step_seconds': float(CONFIG['simulator'].get('live_step_size', .004)),
                        'solver_iterations': int(CONFIG['simulator'].get('live_solver_iterations', 30)),
                        'roller_anchor': 'one_dynamic_shaft_link_revolute_to_world'},
            'transport_mode': 'physical_roller_friction', 'replenishment': 'outfeed_to_clear_infeed_reset',
            'outfeed_policy': {'observed_gate_x': 2.05, 'replenishment_gate_x': 2.35,
                               'scope': 'classification_and_handoff_on_outfeed_rollers',
                               'receiving_trays': 'layout_only_not_a_verified_physical_bin_transfer'},
            'objects': items, 'zones': [{'id': 'infeed', 'position': [INFEED_X, 0, PRODUCT_Z]},
                {'id': 'inspection', 'position': [INSPECTION_X, 0, PRODUCT_Z]},
                {'id': 'outfeed', 'position': [OUTFEED_X, 0, PRODUCT_Z]}],
            'bounds': {'min': [-14.1, -5, 0], 'max': [4.9, 5, 4]}}
