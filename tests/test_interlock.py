"""Validate safety threshold semantics without depending on simulator fault labels."""
import numpy as np
from factory.pdm.model import Detector


def test_score_is_bounded_and_danger_is_lower_than_warning():
    """A high measured feature error must produce danger; a small one stays normal."""
    detector=object.__new__(Detector)
    detector.calibration={'chart':[0,1,2,3,4],'ae':[0,1,2,3,4]}
    detector.thresholds={'chart':.9,'ae':.9}
    detector.errors=lambda feature: (feature[0],feature[1])
    normal=detector.infer(np.array([0,0]))
    danger=detector.infer(np.array([3,3]))
    assert normal['status']=='normal' and normal['health_index']==95
    assert danger['status']=='danger' and danger['health_index']==15
    maximum=detector.infer(np.array([100,100]))
    assert maximum['anomaly_score']==1 and maximum['health_index']==0
