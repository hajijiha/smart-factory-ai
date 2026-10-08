"""CPU YOLOv8 ONNX detector with explicit preprocessing and non-max suppression."""
import cv2
import numpy as np
import onnxruntime as ort
from factory.common import CONFIG


class VisionDetector:
    """Evaluate the fine-tuned YOLOv8 model using an optimized CPU session."""
    def __init__(self, path):
        """Load the graph with a fixed thread budget and configured thresholds."""
        options = ort.SessionOptions()
        options.intra_op_num_threads = CONFIG['vision']['threads']
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path),options,providers=['CPUExecutionProvider'])
        self.input = self.session.get_inputs()[0].name
        self.size = CONFIG['vision']['image_size']
    def infer(self, image):
        """Include BGR→RGB, resize, normalization, model inference and NMS."""
        h,w = image.shape[:2]
        resized = cv2.resize(image,(self.size,self.size))
        tensor = np.ascontiguousarray(resized[:,:,::-1].transpose(2,0,1)[None],dtype=np.float32)/255
        raw = self.session.run(None,{self.input:tensor})[0][0].T
        scores = raw[:,4:]
        classes = scores.argmax(axis=1)
        confidence = scores.max(axis=1)
        mask = confidence >= CONFIG['vision']['confidence']
        boxes, classes, confidence = raw[mask,:4].copy(),classes[mask],confidence[mask]
        if len(boxes) == 0:
            return []
        boxes[:,0] -= boxes[:,2]/2
        boxes[:,1] -= boxes[:,3]/2
        kept = cv2.dnn.NMSBoxesBatched(boxes.tolist(),confidence.tolist(),classes.tolist(),
                      CONFIG['vision']['confidence'],CONFIG['vision']['iou'])
        result = []
        for i in np.asarray(kept).reshape(-1):
            x,y,bw,bh = boxes[i]
            result.append({'defect_type':CONFIG['vision']['classes'][int(classes[i])],
                    'class_id':int(classes[i]),'confidence':float(confidence[i]),
                    'bbox':[float(np.clip(x*w/self.size,0,w)),float(np.clip(y*h/self.size,0,h)),
                            float(np.clip((x+bw)*w/self.size,0,w)),float(np.clip((y+bh)*h/self.size,0,h))]})
        return result
