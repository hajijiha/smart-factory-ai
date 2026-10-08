"""Capture reproducible CPU/container/software evaluation metadata."""
import json
import os
import platform
from pathlib import Path
import cv2
import numpy
import torch
import ultralytics
import onnxruntime
from factory.common import CONFIG


def record():
    """Read non-sensitive CPU/OS facts from the actual experiment container."""
    cpu=next((line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')),'unknown')
    memory=next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:'))
    data={'cpu':cpu,'logical_cpus':os.cpu_count(),'visible_memory_gib':int(memory)/1024**2,
          'os_release':Path('/etc/os-release').read_text(),'kernel':platform.release(),
          'python':platform.python_version(),'torch':torch.__version__,'numpy':numpy.__version__,
          'opencv':cv2.__version__,'ultralytics':ultralytics.__version__,'onnxruntime':onnxruntime.__version__,
          'gpu_used':False,'onnx_intra_op_threads':CONFIG['vision']['threads'],'benchmark_batch':1}
    output=Path(CONFIG['paths']['reports'])/'hardware.json'
    output.write_text(json.dumps(data,indent=2))
    print(json.dumps(data,indent=2))


if __name__=='__main__':
    record()
