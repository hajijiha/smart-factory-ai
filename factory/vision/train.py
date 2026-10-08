"""Fine-tune YOLOv8n on Gazebo images, evaluate held-out scenarios and benchmark CPU."""
import argparse
import json
import platform
import shutil
import time
from pathlib import Path
import cv2
import numpy as np
import torch
from ultralytics import YOLO
from factory.common import ARTIFACTS, DATA, CONFIG
from factory.vision.inference import VisionDetector
from factory.vision.gradcam import GradCAM


def train(epochs=None):
    """Fit detector weights using only training images and select by validation mAP."""
    torch.set_num_threads(CONFIG['vision']['threads'])
    root = DATA/'vision'
    if not (root/'COMPLETE').exists():
        raise RuntimeError('Generate Gazebo data before training')
    model = YOLO('yolov8n.pt')
    model.train(data=str(root/'dataset.yaml'),imgsz=CONFIG['vision']['image_size'],
                epochs=epochs or CONFIG['training']['epochs'],batch=CONFIG['training']['batch'],
                device='cpu',workers=2,seed=20261008,deterministic=True,
                project=str(DATA/'training'),name='vision',exist_ok=True,
                freeze=10,patience=10,cache=False,plots=True,verbose=False,
                mosaic=.2,close_mosaic=5,degrees=5,translate=.05,scale=.15,
                flipud=.1,fliplr=.5,hsv_h=.01,hsv_s=.15,hsv_v=.15)
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    shutil.copy2(DATA/'training/vision/weights/best.pt',ARTIFACTS/'vision.pt')
    evaluate()


def evaluate():
    """Measure untouched test mAP, PyTorch/ONNX latency and normal/defect Grad-CAM."""
    torch.set_num_threads(CONFIG['vision']['threads'])
    model = YOLO(str(ARTIFACTS/'vision.pt'))
    metrics = model.val(data=str(DATA/'vision/dataset.yaml'),split='test',imgsz=CONFIG['vision']['image_size'],
                        device='cpu',batch=16,workers=2,plots=True,project=str(DATA/'evaluation'),name='vision',exist_ok=True)
    exported = model.export(format='onnx',imgsz=CONFIG['vision']['image_size'],opset=17,simplify=False,device='cpu',dynamic=False)
    if Path(exported).resolve() != (ARTIFACTS/'vision.onnx').resolve():
        shutil.copy2(exported,ARTIFACTS/'vision.onnx')
    detector = VisionDetector(ARTIFACTS/'vision.onnx')
    files = sorted((DATA/'vision/images/test').glob('*.jpg'))
    rng = np.random.default_rng(42)
    chosen = rng.choice(files,size=100,replace=False)
    images = [cv2.imread(str(path)) for path in chosen]
    for image in images[:10]:
        detector.infer(image)
        model.predict(image,imgsz=CONFIG['vision']['image_size'],conf=CONFIG['vision']['confidence'],iou=CONFIG['vision']['iou'],verbose=False,device='cpu')
    torch.set_num_threads(CONFIG['vision']['threads'])
    onnx_times, torch_times = [],[]
    for image in images:
        start = time.perf_counter(); detector.infer(image)
        onnx_times.append((time.perf_counter()-start)*1000)
        start = time.perf_counter(); model.predict(image,imgsz=CONFIG['vision']['image_size'],conf=CONFIG['vision']['confidence'],iou=CONFIG['vision']['iou'],verbose=False,device='cpu')
        torch_times.append((time.perf_counter()-start)*1000)
    output = Path(CONFIG['paths']['reports'])
    output.mkdir(parents=True,exist_ok=True)
    cam = GradCAM(ARTIFACTS/'vision.pt')
    cam_report = []
    for class_id,name in [(-1,'normal'),(0,'scratch'),(1,'dent'),(2,'contamination')]:
        image = cv2.imread(str(DATA/'vision/images/test'/f'{class_id}_00000.jpg'))
        overlay,metadata = cam.render(image,None if class_id < 0 else class_id)
        cv2.imwrite(str(output/f'gradcam_{name}.jpg'),overlay)
        cv2.imwrite(str(output/f'sample_{name}.jpg'),image)
        cam_report.append({'type':name,**metadata})
    report = {'mAP50':float(metrics.box.map50),'mAP50_95':float(metrics.box.map),
              'class_mAP50':dict(zip(CONFIG['vision']['classes'],metrics.box.ap50.tolist())),
              'onnx_mean_ms':float(np.mean(onnx_times)),'onnx_p95_ms':float(np.percentile(onnx_times,95)),
              'onnx_fps':1000/float(np.mean(onnx_times)),
              'pytorch_mean_ms':float(np.mean(torch_times)),'pytorch_fps':1000/float(np.mean(torch_times)),
              'benchmark_images':100,'warmup_images':10,'batch_size':1,'includes':'resize, BGR→RGB, normalize, inference, NMS; excludes JPEG decoding, DB, Grad-CAM',
              'onnx_times_ms':onnx_times,'pytorch_times_ms':torch_times,'gradcam':cam_report,
              'python':platform.python_version(),'torch':torch.__version__,'model':'YOLOv8n',
              'test_scenario':'seed 303, wider camera pitch/height/yaw, distinct lighting and image noise',
              'device':'CPU', 'threads':CONFIG['vision']['threads']}
    (output/'vision.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if not k.endswith('times_ms')},indent=2),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--epochs',type=int)
    parser.add_argument('--evaluate-only',action='store_true')
    args = parser.parse_args()
    evaluate() if args.evaluate_only else train(args.epochs)
