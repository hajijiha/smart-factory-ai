"""Regress configured camera geometry and executable module startup without Gazebo state changes."""
import copy
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from simulator import world


def test_sdf_camera_matches_changed_configuration(monkeypatch):
    """Changing camera FOV and image dimensions must change the actual generated SDF."""
    configuration = copy.deepcopy(world.CONFIG)
    configuration['simulator']['camera_fov'] = 1.05
    configuration['simulator']['camera_size'] = 480
    monkeypatch.setattr(world, 'CONFIG', configuration)
    document = ET.fromstring(world.build())
    camera = document.find('./world/model[@name="camera"]/link/sensor/camera')
    assert camera is not None
    assert float(camera.findtext('horizontal_fov')) == 1.05
    assert int(camera.findtext('image/width')) == 480
    assert int(camera.findtext('image/height')) == 480


def test_world_module_invocation_writes_valid_sdf_without_signal_shadowing(tmp_path):
    """The deployed module invocation must run with standard-library signal intact."""
    destination = tmp_path/'factory.world'
    result = subprocess.run(
        [sys.executable, '-m', 'simulator.world', str(destination)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    document = ET.parse(destination)
    camera = document.find('./world/model[@name="camera"]/link/sensor/camera')
    assert camera is not None
    assert float(camera.findtext('horizontal_fov')) == world.CONFIG['simulator']['camera_fov']
    assert int(camera.findtext('image/width')) == world.CONFIG['simulator']['camera_size']
    assert int(camera.findtext('image/height')) == world.CONFIG['simulator']['camera_size']
