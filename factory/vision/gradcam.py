"""Detection Grad-CAM using the final convolution of the YOLOv8 backbone SPPF."""
import cv2
import numpy as np
import torch
from ultralytics import YOLO
from factory.common import CONFIG


class GradCAM:
    """Visualize the gradient of a raw pre-NMS class score, not a handcrafted heatmap."""
    def __init__(self, path):
        """Hook the final backbone Conv block after its BatchNorm and SiLU."""
        self.model = YOLO(str(path)).model.cpu().eval()
        for parameter in self.model.parameters():
            parameter.requires_grad_(True)
        for module in self.model.modules():
            if hasattr(module, 'inplace'):
                module.inplace = False
        self.layer = self.model.model[9].cv2
        self.activation = None
        self.gradient = None
        self.layer.register_forward_hook(self.on_forward)
        self.layer.register_full_backward_hook(self.on_backward)
    def on_forward(self, module, inputs, output):
        """Retain spatial activation values from the target convolution."""
        self.activation = output.detach()
    def on_backward(self, module, grad_input, grad_output):
        """Retain score gradients from the target convolution."""
        self.gradient = grad_output[0].detach()
    def render(self, image, class_id=None):
        """Backpropagate the strongest requested class score and overlay spatial evidence."""
        size = CONFIG['vision']['image_size']
        resized = cv2.resize(image,(size,size))
        tensor = torch.from_numpy(np.ascontiguousarray(resized[:,:,::-1].transpose(2,0,1)[None])).float()/255
        tensor.requires_grad_(True)
        self.model.zero_grad(set_to_none=True)
        prediction = self.model(tensor)[0]
        scores = prediction[:,4:]
        target = scores.max() if class_id is None else scores[:,class_id,:].max()
        target.backward()
        weights = self.gradient.mean(dim=(2,3),keepdim=True)
        cam = torch.relu((weights*self.activation).sum(dim=1))[0].numpy()
        maximum = float(cam.max())
        informative = maximum > 1e-9
        cam = cam/(maximum+1e-9)
        cam = cv2.resize(cam,(image.shape[1],image.shape[0]))
        colored = cv2.applyColorMap((cam*255).astype(np.uint8),cv2.COLORMAP_JET)
        overlay = cv2.addWeighted(image,.6,colored,.4,0) if informative else image.copy()
        return overlay, {'target_layer':'model.9.cv2 (last backbone Conv2d + BatchNorm + SiLU)',
                         'target_score':float(target.detach()),'requested_class':class_id,
                         'maximum_activation':float(cam.max()),'raw_positive_maximum':maximum,
                         'gradient_absolute_sum':float(self.gradient.abs().sum()),
                         'informative':informative}
