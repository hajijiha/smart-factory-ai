"""Analytical 3D defect extents and camera projection for simulator auto-labels."""
import math
import numpy as np
from scipy.spatial.transform import Rotation
from factory.common import CONFIG


def defect_points(class_id):
    """Return geometric boundary points matching the Gazebo world primitives."""
    if class_id == 0:
        points=[]
        for i in range(3):
            box=np.array([[x,y,z] for x in [-.15,.15] for y in [-.009,.009] for z in [-.0015,.0015]])
            points.extend(Rotation.from_euler('z',.1).apply(box)+[0,i*.022-.022,0])
        return np.asarray(points)
    circles=[]
    primitives=[(0,0,0,.095,.005),(0,0,.003,.061,.007)] if class_id == 1 else [(x,y,.004,.052,.006) for x,y in [(-.04,-.02),(.03,.02),(-.01,.06)]]
    for x,y,z,radius,length in primitives:
        for angle in np.linspace(0,2*np.pi,64,endpoint=False):
            for dz in [-length/2,length/2]:
                circles.append([x+radius*np.cos(angle),y+radius*np.sin(angle),z+dz])
    return np.asarray(circles)


def project_bbox(scene):
    """Project the real defect geometry through Gazebo's pinhole camera into YOLO format."""
    class_id=scene['class_id']
    if class_id<0:
        return None
    points=defect_points(class_id)
    world=Rotation.from_euler('z',scene['yaw']).apply(
        Rotation.from_euler('z',scene['defect_yaw']).apply(points)+[scene['dx'],scene['dy'],.032])+[scene['x'],scene['y'],.58]
    camera=scene['camera_pose']
    local=Rotation.from_euler('xyz',camera[3:]).inv().apply(world-np.array(camera[:3]))
    focal=.5/math.tan(CONFIG['simulator']['camera_fov']/2)
    uv=np.column_stack([.5-focal*local[:,1]/local[:,0],.5-focal*local[:,2]/local[:,0]])
    low,high=np.clip(uv.min(axis=0),0,1),np.clip(uv.max(axis=0),0,1)
    return [int(class_id),float((low[0]+high[0])/2),float((low[1]+high[1])/2),float(high[0]-low[0]),float(high[1]-low[1])]
