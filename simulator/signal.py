"""Fault injection on a baseline derived from Gazebo's observed rotating joint."""
import numpy as np
from factory.pdm.features import bearing_frequencies


def vibration(level, omega, phase, rng, noise=.012, sampling_hz=2048, samples=2048,
              fault_type=None, parameters=None):
    """Mix imbalance, harmonics and ball-pass impulses with physical motor rotation."""
    if parameters is not None:
        return vibration_v2(level, omega, phase, rng, fault_type, parameters, sampling_hz, samples)
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


def vibration_v2(level, omega, phase, rng, fault_type, parameters, sampling_hz=2048, samples=2048):
    """Diverse proxy signals with nuisance noise independent of fault labels.

    Frequencies follow the observed Gazebo rotation. Fault amplitudes are injected,
    not measurements of a damaged bearing or a validated wear process.
    """
    if not 0 <= level <= 10:
        raise ValueError('Fault level must be between zero and ten')
    if fault_type not in ('normal', 'imbalance', 'looseness', 'outer_race', 'inner_race', 'mixed'):
        raise ValueError(f'Unknown fault type: {fault_type}')
    if level == 0 and fault_type != 'normal':
        raise ValueError('Normal windows must have normal fault type')
    p = parameters
    t = np.arange(samples) / sampling_hz
    rotation = max(abs(omega) / (2 * np.pi), .01)
    bpfo, bpfi = bearing_frequencies(rotation)
    # Broad normal load/gain variation prevents one clean normal prototype.
    gain = p['gain'] * float(rng.uniform(.97, 1.03))
    baseline = .16 * (rotation / 25) ** 2 * p['load'] * np.sin(omega * t + phase)
    baseline += .018 * p['load'] * np.sin(2 * omega * t + phase * .4)
    effect = np.zeros(samples)
    severity = level / 10 * float(rng.uniform(.85, 1.15))
    if fault_type in ('imbalance', 'mixed'):
        effect += .60 * severity * np.sin(omega * t + phase)
    if fault_type in ('looseness', 'mixed'):
        effect += .40 * severity ** 1.4 * np.sin(2 * omega * t + phase)
        effect += .25 * severity * np.sin(3 * omega * t - phase)
    frequencies = []
    if fault_type in ('outer_race', 'mixed'):
        frequencies.append(bpfo)
    if fault_type in ('inner_race', 'mixed'):
        frequencies.append(bpfi)
    for frequency in frequencies:
        pulse_phase = (t * frequency + phase / (2 * np.pi)) % 1
        effect += 1.1 * severity * np.exp(-pulse_phase * p['resonance_decay']) * np.cos(2 * np.pi * p['resonance_hz'] * t)
    white = rng.normal(0, p['noise'], samples)
    colored = np.convolve(rng.normal(0, 1, samples + 8), np.ones(9) / 3, mode='valid') * p['colored_noise']
    return gain * (baseline + effect) + white + colored + p['dc_offset']
