"""Generate a real Gazebo SDF conveyor, rotating joints, 3D defects and ROS2 camera."""
import sys
from pathlib import Path
from factory.common import CONFIG


def visual(name, shape, dimensions, color, pose='0 0 0 0 0 0'):
    """Create a colored 3D visual primitive."""
    geometry = f'<box><size>{dimensions}</size></box>' if shape == 'box' else f'<cylinder><radius>{dimensions[0]}</radius><length>{dimensions[1]}</length></cylinder>'
    return f'<visual name="{name}"><pose>{pose}</pose><geometry>{geometry}</geometry><material><ambient>{color} 1</ambient><diffuse>{color} 1</diffuse></material></visual>'


def static(name, pose, visuals):
    """Create a static model whose visual poses can be randomized by the world plugin."""
    return f'<model name="{name}"><static>true</static><pose>{pose}</pose><link name="body">{visuals}</link></model>'


def motor(name, pose):
    """Create a physical revolute rotor anchored to a fixed stator."""
    return f'''<model name="{name}"><pose>{pose}</pose>
    <link name="base"><inertial><mass>2</mass><inertia><ixx>1</ixx><iyy>1</iyy><izz>1</izz></inertia></inertial></link>
    <joint name="fixed" type="fixed"><parent>world</parent><child>base</child></joint>
    <link name="shaft"><inertial><mass>1</mass><inertia><ixx>.02</ixx><iyy>.02</iyy><izz>.02</izz></inertia></inertial>
    {visual('visual', 'cylinder', (.12,.25), '.35 .4 .45')}</link>
    <joint name="shaft_joint" type="revolute"><parent>base</parent><child>shaft</child><axis><xyz>0 0 1</xyz></axis></joint></model>'''


def build():
    """Build defect geometry rather than drawing the final camera image in Python."""
    scratch = ''.join(visual(f'groove{i}', 'box', '.30 .018 .003', '.05 .05 .05', f'0 {i*.022-.022} 0 0 0 .1') for i in range(3))
    dent = visual('rim', 'cylinder', (.095,.005), '.45 .45 .46') + visual('pit', 'cylinder', (.061,.007), '.12 .13 .15', '0 0 .003 0 0 0')
    contamination = ''.join(visual(f'oil{i}', 'cylinder', (.052,.006), '.20 .09 .025', f'{x} {y} .004 0 0 0') for i,(x,y) in enumerate([(-.04,-.02),(.03,.02),(-.01,.06)]))
    models = static('conveyor', '0 0 .48 0 0 0', visual('visual','box','2.5 1.8 .15','.18 .23 .27'))
    models += static('product','0 0 .58 0 0 0',visual('visual','box','.9 .7 .06','.75 .76 .77'))
    for i, geometry in enumerate([scratch,dent,contamination]):
        models += static(f'defect_{i}','0 0 -10 0 0 0',geometry)
    models += motor('motor','1.5 0 .5 0 1.57 0')+motor('roller','-.9 0 .35 0 1.57 0')
    return f'''<?xml version="1.0"?><sdf version="1.6"><world name="factory">
      <physics type="ode"><max_step_size>.001</max_step_size><real_time_update_rate>0</real_time_update_rate></physics>
      <gravity>0 0 -9.81</gravity><scene><ambient>.35 .35 .35 1</ambient><background>.12 .13 .16 1</background><shadows>false</shadows></scene>
      <light name="sun" type="directional"><pose>0 0 4 0 0 0</pose><diffuse>.8 .8 .8 1</diffuse><specular>.1 .1 .1 1</specular><direction>-.3 .2 -1</direction></light>
      {models}<model name="camera"><static>true</static><pose>0 0 2.8 0 1.57079632679 0</pose>
      <link name="camera_link"><sensor name="inspection" type="camera"><always_on>true</always_on><update_rate>30</update_rate>
      <camera><horizontal_fov>{CONFIG['simulator']['camera_fov']}</horizontal_fov><image><width>{CONFIG['simulator']['camera_size']}</width><height>{CONFIG['simulator']['camera_size']}</height><format>R8G8B8</format></image><clip><near>.02</near><far>30</far></clip></camera>
      <plugin name="ros_camera" filename="libgazebo_ros_camera.so"><ros><namespace>/factory</namespace><remapping>image_raw:=image</remapping><remapping>camera_info:=camera_info</remapping></ros><camera_name>inspection</camera_name><frame_name>camera_link</frame_name></plugin>
      </sensor></link></model><plugin name="factory_world" filename="libfactory_world.so"/>
      </world></sdf>'''


if __name__ == '__main__':
    Path(sys.argv[1]).write_text(build())
