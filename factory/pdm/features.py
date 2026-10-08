"""Physical bearing frequencies and time/frequency vibration features."""
import numpy as np
from scipy.stats import kurtosis
from factory.common import CONFIG

NAMES = ['mean', 'rms', 'peak', 'crest_factor', 'kurtosis', 'dominant_frequency',
         'spectral_energy', 'bpfo_amplitude', 'bpfi_amplitude']


def bearing_frequencies(rotation_hz):
    """Calculate outer/inner race ball-pass frequencies from bearing geometry."""
    c = CONFIG['sensor']
    ratio = c['ball_diameter_mm'] / c['pitch_diameter_mm'] * np.cos(np.deg2rad(c['contact_angle_deg']))
    return c['bearing_balls'] / 2 * rotation_hz * (1 - ratio), c['bearing_balls'] / 2 * rotation_hz * (1 + ratio)


def extract(wave, sampling_hz=2048, rotation_hz=25):
    """Extract nine features and a Hann-windowed, amplitude-corrected FFT."""
    x = np.asarray(wave, dtype=np.float64)
    rms = float(np.sqrt(np.mean(x*x)))
    peak = float(np.max(np.abs(x)))
    window = np.hanning(len(x))
    amplitude = np.abs(np.fft.rfft((x - x.mean()) * window)) * 2 / window.sum()
    frequencies = np.fft.rfftfreq(len(x), 1/sampling_hz)
    bpfo, bpfi = bearing_frequencies(rotation_hz)
    def band(target):
        """Take the largest amplitude within two frequency bins of a target."""
        selection = np.abs(frequencies-target) <= max(2*sampling_hz/len(x), 2)
        return float(amplitude[selection].max())
    values = np.array([x.mean(), rms, peak, peak/max(rms, 1e-9), kurtosis(x, fisher=False),
                       frequencies[1+np.argmax(amplitude[1:])], np.sum(amplitude**2), band(bpfo), band(bpfi)])
    spectrum = {'frequencies': frequencies[:401].tolist(), 'amplitudes': amplitude[:401].tolist(),
                'bpfo_hz': bpfo, 'bpfi_hz': bpfi}
    return values, spectrum


def transform(features):
    """Compress positive features; keep the signed signal mean."""
    features = np.asarray(features).copy()
    features[..., 1:] = np.log1p(np.maximum(features[..., 1:], 0))
    return features
