"""Independently measure CPU deployment throughput on 100 frozen test images."""
import hashlib
import json
import platform
import sys
import time
from pathlib import Path
import cv2
import numpy as np
import torch
from ultralytics import YOLO
sys.path.insert(0, '/review')
from factory.vision.inference import VisionDetector


def review():
    """Use equal images, CPU threads and threshold settings without any training."""
    files = sorted(Path('/baseline/vision/images/test').glob('*.jpg'))
    chosen = np.random.default_rng(42).choice(files, size=100, replace=False)
    images = [cv2.imread(str(path)) for path in chosen]
    onnx = VisionDetector('/review/artifacts/vision.onnx')
    model = YOLO('/review/artifacts/vision.pt')
    torch.set_num_threads(4)
    for image in images[:10]:
        onnx.infer(image)
        model.predict(image,imgsz=256,conf=.25,iou=.45,verbose=False,device='cpu')
    after_warmup = torch.get_num_threads()
    torch.set_num_threads(4)
    intervals = {'onnx': [], 'pytorch': []}
    for image in images:
        start = time.perf_counter()
        onnx.infer(image)
        intervals['onnx'].append((time.perf_counter()-start)*1000)
        start = time.perf_counter()
        model.predict(image,imgsz=256,conf=.25,iou=.45,verbose=False,device='cpu')
        intervals['pytorch'].append((time.perf_counter()-start)*1000)
    fps = {engine:1000/float(np.mean(values)) for engine,values in intervals.items()}
    return {'passed':fps['onnx'] >= 20, 'benchmark_images':100, 'warmup_images':10,
            'onnx_fps':fps['onnx'], 'pytorch_fps':fps['pytorch'],
            'mean_ms':{engine:float(np.mean(values)) for engine,values in intervals.items()},
            'latencies_ms':intervals, 'torch_threads_after_warmup_before_reset':after_warmup,
            'torch_threads_measured':torch.get_num_threads(), 'torch_version':torch.__version__,
            'python_version':platform.python_version(), 'cpu_only':True, 'batch_size':1,
            'scope':'Resize, BGR/RGB normalization, inference, NMS; excludes JPEG decode, file I/O, Grad-CAM, network and DB',
            'test_image_sha256':[hashlib.sha256(path.read_bytes()).hexdigest() for path in chosen]}


if __name__=='__main__':
    result=review()
    Path('/independent/independent-vision-speed.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({key:value for key,value in result.items() if key not in ('latencies_ms','test_image_sha256')},indent=2))
