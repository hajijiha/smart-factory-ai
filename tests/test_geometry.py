"""Check analytical labels against a centered top-down camera geometry."""
import math
from simulator.geometry import project_bbox


def test_centered_dent_projected_scale():
    """A circular 0.19 m defect at 2.188 m depth has the pinhole-projected size."""
    scene={'class_id':1,'yaw':0,'defect_yaw':0,'dx':0,'dy':0,'x':0,'y':0,'camera_pose':[0,0,2.8,0,math.pi/2,0]}
    label=project_bbox(scene)
    assert abs(label[1]-.5)<.001 and abs(label[2]-.5)<.001
    expected=.19/(2*(2.8-.612)*math.tan(.75/2))
    assert abs(label[3]-expected)<.002


def test_normal_frame_has_no_box():
    """Normal products must not receive a fabricated defect label."""
    assert project_bbox({'class_id':-1}) is None
