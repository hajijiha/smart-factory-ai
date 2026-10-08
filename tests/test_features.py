"""Validate frequency physics and FFT amplitude, independent of service implementation."""
import numpy as np
from factory.pdm.features import bearing_frequencies,extract


def test_bearing_formula_and_resolution():
    """A 25 Hz shaft produces physically ordered BPFO/BPFI and exact FFT bins."""
    outer,inner=bearing_frequencies(25)
    assert 75<outer<85 and 115<inner<125
    t=np.arange(2048)/2048
    features,spectrum=extract(.5*np.sin(2*np.pi*25*t))
    assert features[5]==25
    assert abs(features[1]-.5/np.sqrt(2))<.001
    assert abs(spectrum['amplitudes'][25]-.5)<.002


def test_nyquist_of_modeled_resonance():
    """The 350 Hz resonance and BPFI harmonics remain below the 1024 Hz Nyquist limit."""
    outer,inner=bearing_frequencies(25)
    assert 350<2048/2 and 3*inner<2048/2
