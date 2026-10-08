"""Independent first-start and explicit NMS checks without the live vision service."""
from pathlib import Path
import numpy as np
import pytest
from factory.vision import service
from factory.vision.inference import VisionDetector


class WorkerStopped(Exception):
    """Stop a mocked image worker after successful first-start initialization."""


def test_first_start_exports_missing_onnx_using_configured_size(monkeypatch, tmp_path):
    """A weights-only distribution must initialize successfully without a prior ONNX export."""
    (tmp_path/'vision.pt').write_bytes(b'test-checkpoint')
    exported = []

    class FakeYOLO:
        """Record export arguments without loading or training a network."""
        def __init__(self, path):
            """Check the intended locally distributed checkpoint path."""
            assert Path(path) == tmp_path/'vision.pt'

        def export(self, **arguments):
            """Create a temporary graph to let first-start initialization continue."""
            exported.append(arguments)
            (tmp_path/'vision.onnx').write_bytes(b'test-graph')
            return str(tmp_path/'vision.onnx')

    class FakeQueue:
        """Prevent all waiting and network input after initialization."""
        def __init__(self, maxsize):
            """Validate the service queue is bounded."""
            assert maxsize > 0

        def get(self):
            """Signal that initialization reached the worker loop."""
            raise WorkerStopped()

        def put_nowait(self, message):
            """Do not connect to external image input."""
            raise AssertionError('No MQTT message should be received')

    def detector(path):
        """Require the generated graph to be present before detector initialization."""
        assert Path(path).read_bytes() == b'test-graph'
        return object()

    monkeypatch.setattr(service, 'ARTIFACTS', tmp_path)
    monkeypatch.setattr(service, 'DATA', tmp_path)
    monkeypatch.setattr(service, 'YOLO', FakeYOLO)
    monkeypatch.setattr(service, 'VisionDetector', detector)
    monkeypatch.setattr(service, 'GradCAM', lambda path: object())
    monkeypatch.setattr(service.queue, 'Queue', FakeQueue)
    monkeypatch.setattr(service, 'connect', lambda *args: object())
    with pytest.raises(WorkerStopped):
        service.main()
    assert len(exported) == 1
    assert exported[0]['imgsz'] == service.CONFIG['vision']['image_size']
    assert exported[0]['device'] == 'cpu'


def test_onnx_preprocessing_and_class_aware_nms():
    """Overlapping boxes of different defect classes survive while class duplicates are suppressed."""
    detector = object.__new__(VisionDetector)
    detector.input = 'images'
    detector.size = 256
    predictions = np.asarray([
        [128,128,80,80,.90,.05,.05],
        [129,129,80,80,.80,.05,.05],
        [128,128,80,80,.05,.85,.05],
    ], dtype=np.float32)

    class Session:
        """Inspect actual preprocessing and supply known YOLO-shaped predictions."""
        def run(self, outputs, inputs):
            """A pure blue BGR image must become RGB channels [0,0,1]."""
            tensor = inputs['images']
            assert tensor.shape == (1,3,256,256)
            assert tensor.dtype == np.float32
            assert np.all(tensor[:,0] == 0) and np.all(tensor[:,1] == 0)
            assert np.all(tensor[:,2] == 1)
            return [predictions.T[None]]

    detector.session = Session()
    image = np.zeros((320,320,3), dtype=np.uint8)
    image[:,:,0] = 255
    detections = detector.infer(image)
    assert len(detections) == 2
    assert {detection['class_id'] for detection in detections} == {0,1}
    assert all(np.allclose(detection['bbox'], [110,110,210,210], atol=1e-5) for detection in detections)
