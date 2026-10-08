"""Fault injection on a baseline derived from Gazebo's observed rotating joint."""
import numpy as np
from factory.pdm.features import bearing_frequencies


def vibration(level, omega, phase, rng, noise=.012, sampling_hz=2048, samples=2048):
    """Mix imbalance, harmonics and ball-pass impulses with physical motor rotation."""
    rotation = max(abs(omega)/(2*np.pi), .01)
    t = np.arange(samples)/sampling_hz
    bpfo, bpfi = bearing_frequencies(rotation)
    amplitude = .16 * (rotation/25)**2
    baseline = amplitude*np.sin(omega*t+phase)
    severity = level/10
    imbalance = .60*severity*np.sin(omega*t+phase)
    looseness = .40*severity**1.4*np.sin(2*omega*t)+.25*severity*np.sin(3*omega*t)
    impulses = np.zeros(samples)
    for frequency in [bpfo,bpfi]:
        pulse_phase = (t*frequency+phase/(2*np.pi)) % 1
        impulses += 1.1*severity*np.exp(-pulse_phase*22)*np.cos(2*np.pi*350*t)
    white = rng.normal(0, noise+.16*severity, samples)
    return baseline+imbalance+looseness+impulses+white
