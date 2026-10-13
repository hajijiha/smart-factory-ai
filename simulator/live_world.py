"""Build the production world: contact-driven rollers, moving plates and real cameras."""
import math
import os
from factory.layout import get_layout, PRODUCT_SIZE
from factory.settings import CONFIG


def _numbers(values):
    """Encode finite primitive coordinates for SDF XML."""
    return ' '.join(f'{value:.9g}' for value in values)


def _geometry(item):
    """Translate a shared layout primitive into SDF geometry."""
    if item['shape'] == 'cylinder':
        return f'<cylinder><radius>{item["size"][0]}</radius><length>{item["size"][1]}</length></cylinder>'
    return f'<box><size>{_numbers(item["size"])}</size></box>'


def _visual(item, name='visual', pose='0 0 0 0 0 0'):
    """Create the same material and geometry shown by the shared web layout."""
    color = item['color'].lstrip('#')
    rgba = _numbers([int(color[i:i+2], 16)/255 for i in (0, 2, 4)])+' 1'
    return f'<visual name="{name}"><pose>{pose}</pose><geometry>{_geometry(item)}</geometry><material><ambient>{rgba}</ambient><diffuse>{rgba}</diffuse><specular>.06 .06 .06 1</specular></material></visual>'


def _collision(item):
    """Use ODE contacts with friction along both tangent directions."""
    return f'''<collision name="collision"><geometry>{_geometry(item)}</geometry>
    <surface><friction><ode><mu>.85</mu><mu2>.85</mu2><slip1>0</slip1><slip2>0</slip2></ode></friction>
    <contact><ode><kp>100000</kp><kd>20</kd><max_vel>.01</max_vel><min_depth>.0001</min_depth></ode></contact>
    <bounce><restitution_coefficient>0</restitution_coefficient><threshold>100</threshold></bounce></surface></collision>'''


def _defect_parts(kind):
    """Keep the three physical defect classes in fixed attached visual slots."""
    z = PRODUCT_SIZE[2]/2+.004
    if kind == 0:
        parts = [({'shape': 'box', 'size': [.30, .018, .003], 'color': '#151515'}, [0, i*.022-.022, z, 0, 0, .1]) for i in range(3)]
    elif kind == 1:
        parts = [({'shape': 'cylinder', 'size': [r, .005], 'color': color}, [0, 0, z+extra, 0, 0, 0]) for r, color, extra in [(.095, '#737376', 0), (.061, '#202126', .003)]]
    elif kind == 2:
        parts = [({'shape': 'cylinder', 'size': [.052, .006], 'color': '#331706'}, [x, y, z, 0, 0, 0]) for x, y in [(-.04, -.02), (.03, .02), (-.01, .06)]]
    else:
        parts = []
    return parts


def _defects():
    """Start all slots hidden; the seeded physical controller selects the current defect."""
    visuals = []
    for kind in range(3):
        for index, (part, pose) in enumerate(_defect_parts(kind)):
            visual = _visual(part, f'defect_{kind}_{index}', _numbers(pose))
            visuals.append(visual.replace('</visual>', '<transparency>1</transparency></visual>'))
    return ''.join(visuals)


def _model(item):
    """Give dynamic plates and rollers real mass, collisions, and fixed/revolute joints."""
    name = item['id']
    pose = _numbers(item['position']+item['rotation'])
    if item['kind'] == 'product':
        mass = .35
        x, y, z = item['size']
        inertia = [mass*(y*y+z*z)/12, mass*(x*x+z*z)/12, mass*(x*x+y*y)/12]
        return f'''<model name="{name}"><pose>{pose}</pose><link name="body"><inertial><mass>{mass}</mass><inertia><ixx>{inertia[0]}</ixx><iyy>{inertia[1]}</iyy><izz>{inertia[2]}</izz></inertia></inertial>{_visual(item)}{_defects()}{_collision(item)}<velocity_decay><linear>.03</linear><angular>.1</angular></velocity_decay></link></model>'''
    if item['kind'] in ('roller', 'motor'):
        marker = {'shape': 'box', 'size': [.11, .012, .025], 'color': '#e4bc4c'}
        if item['kind'] == 'roller':
            return f'''<model name="{name}"><pose>{pose}</pose>
            <link name="shaft"><inertial><mass>.35</mass><inertia><ixx>.04</ixx><iyy>.04</iyy><izz>.002</izz></inertia></inertial><velocity_decay><linear>0</linear><angular>0</angular></velocity_decay>{_visual(item)}{_visual(marker, 'rotation_mark', '0 .059 0 0 0 0')}{_collision(item)}</link>
            <joint name="shaft_joint" type="revolute"><parent>world</parent><child>shaft</child><axis><xyz>0 0 1</xyz><limit><lower>-1e16</lower><upper>1e16</upper><effort>10000</effort><velocity>1000</velocity></limit><dynamics><damping>0</damping></dynamics></axis></joint></model>'''
        return f'''<model name="{name}"><pose>{pose}</pose><link name="base"><inertial><mass>1</mass><inertia><ixx>.02</ixx><iyy>.02</iyy><izz>.02</izz></inertia></inertial></link>
        <joint name="fixed" type="fixed"><parent>world</parent><child>base</child></joint>
        <link name="shaft"><inertial><mass>.35</mass><inertia><ixx>.04</ixx><iyy>.04</iyy><izz>.002</izz></inertia></inertial><velocity_decay><linear>0</linear><angular>0</angular></velocity_decay>{_visual(item)}{_visual(marker, 'rotation_mark', '0 .059 0 0 0 0')}{_collision(item) if item['kind']=='roller' else ''}</link>
        <joint name="shaft_joint" type="revolute"><parent>base</parent><child>shaft</child><axis><xyz>0 0 1</xyz><limit><lower>-1e16</lower><upper>1e16</upper><effort>10000</effort><velocity>1000</velocity></limit><dynamics><damping>0</damping></dynamics></axis></joint></model>'''
    collisions = _collision(item) if item['kind'] in ('floor', 'frame', 'outfeed') else ''
    return f'<model name="{name}"><static>true</static><pose>{pose}</pose><link name="body">{_visual(item)}{collisions}</link></model>'


def _camera(name, position, rotation, width, height, rate, fov):
    """Use a Gazebo ROS camera for observed pixels instead of synthetic browser images."""
    return f'''<model name="{name}_camera"><static>true</static><pose>{_numbers(position+rotation)}</pose><link name="camera_link">
    <sensor name="{name}" type="camera"><always_on>true</always_on><update_rate>{rate}</update_rate><camera><horizontal_fov>{fov}</horizontal_fov><image><width>{width}</width><height>{height}</height><format>R8G8B8</format></image><clip><near>.02</near><far>80</far></clip></camera>
    <plugin name="ros_{name}_camera" filename="libgazebo_ros_camera.so"><ros><namespace>/factory</namespace></ros><camera_name>{name}</camera_name><frame_name>{name}_camera_link</frame_name></plugin></sensor></link></model>'''


def build_live():
    """Generate the realtime production world while keeping accelerated dataset worlds separate."""
    layout = get_layout()
    defect_mode = os.environ.get('FACTORY_LIVE_DEFECT_MODE', 'operational')
    if defect_mode not in ('operational', 'balanced-evaluation'):
        raise ValueError('FACTORY_LIVE_DEFECT_MODE must be operational or balanced-evaluation')
    step = float(CONFIG['simulator'].get('live_step_size', .004))
    iterations = int(CONFIG['simulator'].get('live_solver_iterations', 30))
    if not .001 <= step <= .005 or not 10 <= iterations <= 200:
        raise ValueError('Live physics needs a 1–5 ms step and 10–200 solver iterations')
    models = ''.join(_model(item) for item in layout['objects'])
    models += _camera('inspection', [0, 0, 2.8], [0, math.pi/2, 0], CONFIG['simulator']['camera_size'], CONFIG['simulator']['camera_size'], 6, CONFIG['simulator']['camera_fov'])
    models += _camera('overview', [-4.6, -7.5, 7.0], [0, .65, math.pi/2], 640, 360, 3, 1.6)
    return f'''<?xml version="1.0"?><sdf version="1.6"><world name="factory">
    <physics type="ode"><max_step_size>{step}</max_step_size><real_time_update_rate>{1/step}</real_time_update_rate><real_time_factor>1</real_time_factor><ode><solver><type>quick</type><iters>{iterations}</iters><sor>1.3</sor></solver><constraints><cfm>0</cfm><erp>.2</erp><contact_max_correcting_vel>1000</contact_max_correcting_vel><contact_surface_layer>.0001</contact_surface_layer></constraints></ode></physics>
    <gravity>0 0 -9.81</gravity><scene><ambient>.55 .55 .55 1</ambient><background>.12 .13 .16 1</background><shadows>false</shadows></scene>
    <light name="sun" type="directional"><pose>0 0 4 0 0 0</pose><diffuse>.8 .8 .8 1</diffuse><specular>.1 .1 .1 1</specular><direction>-.3 .2 -1</direction></light>
    {models}<plugin name="factory_world" filename="libfactory_world.so"><live_mode>true</live_mode><world_version>{layout['world_version']}</world_version><product_count>{sum(o['kind']=='product' for o in layout['objects'])}</product_count><roller_count>{sum(o['kind']=='roller' for o in layout['objects'])}</roller_count><defect_mode>{defect_mode}</defect_mode><seed>{CONFIG['simulator']['seed']}</seed><initial_fault_level>{CONFIG['simulator']['initial_fault_level']}</initial_fault_level><diagnostic_motor_rpm>{CONFIG['simulator'].get('live_motor_rpm', 100*60/(2*math.pi))}</diagnostic_motor_rpm></plugin></world></sdf>'''
