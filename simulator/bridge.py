"""Acquire actual Gazebo camera/joint output, randomize scenes, and publish MQTT events."""
import base64
import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from pathlib import Path
import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import String
from factory.common import CONFIG, DATA, connect, publish, utc_now
from factory.pdm.features import extract
from simulator.signal import vibration
from simulator.geometry import project_bbox
from simulator.sensor_dataset import generate_sensor


def random_scene(rng, class_id, split, scene_id):
    """Choose distinct seeded train/validation/test camera and lighting scenarios."""
    variation = {'train':.07, 'val':.09, 'test':.12}[split]
    return {'scene_id':scene_id, 'class_id':int(class_id), 'x':float(rng.uniform(-.15,.15)),
            'y':float(rng.uniform(-.15,.15)), 'yaw':float(rng.uniform(-4*variation,4*variation)),
            'dx':float(rng.uniform(-.2,.2)), 'dy':float(rng.uniform(-.18,.18)),
            'defect_yaw':float(rng.uniform(-.8,.8)),
            'camera_pose':[float(rng.uniform(-variation,variation)),float(rng.uniform(-variation,variation)),
                float(rng.uniform(2.6-variation,2.95+variation)),0,math.pi/2+float(rng.uniform(-variation,variation)),float(rng.uniform(-variation,variation))],
            'product_color':[float(rng.uniform(.65,.85))]*3,
            'background_color':rng.uniform(.10,.30,3).tolist(),
            'light':float(rng.uniform(.55-variation,.95+variation)), 'split':split}


class Acquisition(Node):
    """ROS2 camera/joint subscriber with an acknowledged scene-control channel."""
    def __init__(self):
        """Connect ROS2 acquisition topics and initialize a synchronized frame buffer."""
        super().__init__('factory_acquisition')
        self.image = None
        self.image_time = -1
        self.joint = None
        self.ack = None
        self.condition = threading.Condition()
        self.publisher = self.create_publisher(String, '/factory/scene', 10)
        self.create_subscription(Image, '/factory/inspection/image_raw', self.on_image, qos_profile_sensor_data)
        self.create_subscription(JointState, '/factory/motor_state', self.on_joint, 10)
        self.create_subscription(String, '/factory/scene_ready', self.on_ack, 10)
    def on_image(self, message):
        """Decode Gazebo's RGB camera buffer without generating synthetic 2D substitutes."""
        image = np.frombuffer(message.data, dtype=np.uint8).reshape(message.height,message.step)[:, :message.width*3].reshape(message.height,message.width,3)
        if message.encoding == 'rgb8':
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        with self.condition:
            self.image = image.copy()
            self.image_time = message.header.stamp.sec+message.header.stamp.nanosec/1e9
            self.condition.notify_all()
    def on_joint(self, message):
        """Record the measured Gazebo joint velocity and position as sensor provenance."""
        with self.condition:
            self.joint = {'position':list(message.position), 'velocity':list(message.velocity),
                          'sim_time':message.header.stamp.sec+message.header.stamp.nanosec/1e9}
            self.condition.notify_all()
    def on_ack(self, message):
        """Record physics-thread acknowledgment of the scene command."""
        with self.condition:
            self.ack = json.loads(message.data)
            self.condition.notify_all()
    def capture(self, scene):
        """Wait for an acknowledged scene and a newer rendered camera frame."""
        command = String(data=json.dumps(scene))
        deadline = time.monotonic()+30
        with self.condition:
            sent_at = 0
            while time.monotonic() < deadline:
                if time.monotonic()-sent_at >= 1 and (not self.ack or self.ack.get('scene_id') != scene['scene_id']):
                    self.publisher.publish(command)
                    sent_at = time.monotonic()
                if self.ack and self.ack.get('scene_id') == scene['scene_id'] and self.image is not None and self.joint:
                    if self.image_time >= self.ack['sim_time']+.10:
                        return self.image.copy(),dict(self.joint),self.image_time
                self.condition.wait(.05)
        raise TimeoutError(f'Gazebo frame timeout: scene={scene["scene_id"]}, image_time={self.image_time}, ack={self.ack}; inspect /tmp/gazebo.log')
    def conveyor(self, running):
        """Command the physical conveyor roller, retaining the diagnostic motor on its test stand."""
        self.publisher.publish(String(data=json.dumps({'running':bool(running)})))


def generate(node):
    """Collect reproducible labeled Gazebo data with separate scenario seeds per split."""
    generate_sensor(node)
    root = DATA/'vision'
    manifest_path = root/'manifest.jsonl'
    if not manifest_path.exists():
        root.mkdir(parents=True,exist_ok=True)
        with manifest_path.open('w') as manifest:
            counts = {-1:CONFIG['training']['vision_normal'], **{i:CONFIG['training']['vision_per_defect'] for i in range(3)}}
            number = 0
            for split,ratio,seed in [('train',.70,101),('val',.15,202),('test',.15,303)]:
                rng = np.random.default_rng(seed)
                for class_id,total in counts.items():
                    count = int(total*ratio)
                    for index in range(count):
                        scene = random_scene(rng,class_id,split,f'{split}-{class_id}-{index}')
                        image,joint,sim_time = node.capture(scene)
                        image = np.clip(image.astype(np.float32)+rng.normal(0,1.5 if split=='train' else 2.5,image.shape),0,255).astype(np.uint8)
                        image_path = root/'images'/split/f'{class_id}_{index:05d}.jpg'
                        label_path = root/'labels'/split/f'{class_id}_{index:05d}.txt'
                        image_path.parent.mkdir(parents=True,exist_ok=True)
                        label_path.parent.mkdir(parents=True,exist_ok=True)
                        cv2.imwrite(str(image_path),image)
                        label = project_bbox(scene)
                        label_path.write_text('' if label is None else ' '.join(map(str,label))+'\n')
                        manifest.write(json.dumps({'image':str(image_path.relative_to(root)), 'scene':scene,
                            'joint':joint,'camera_sim_time':sim_time,'timestamp':utc_now(),
                            'sha256':hashlib.sha256(image_path.read_bytes()).hexdigest()})+'\n')
                        number += 1
                        if number % 100 == 0:
                            manifest.flush()
                            logging.info('Gazebo images: %d / %d', number,sum(counts.values()))
        (root/'dataset.yaml').write_text(f'path: {root}\ntrain: images/train\nval: images/val\ntest: images/test\nnames: [scratch, dent, contamination]\n')
        (root/'COMPLETE').write_text(str(number))
    if not (root/'COMPLETE').exists():
        raise RuntimeError('Incomplete image dataset; inspect manifest before resuming or choose a new DATA_DIR')
    publish_counts = {'vision_images':int((root/'COMPLETE').read_text()),'sensor_windows':CONFIG['training']['sensor_normal']+CONFIG['training']['sensor_fault'],
                      'source':'Gazebo Classic 11 + ROS2 Humble camera and ODE joint observations', 'completed_at':utc_now()}
    report = Path(CONFIG['paths']['reports'])/'dataset.json'
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(publish_counts,indent=2))


def live(node):
    """Publish synchronized OT/IT samples and execute MQTT fault/conveyor commands."""
    state = {'level':CONFIG['simulator']['initial_fault_level'],'running':True}
    def handler(topic,message):
        """Apply validated asynchronous commands received from dashboard/controller."""
        if topic == 'factory/command/fault':
            state['level'] = max(0,min(10,int(message['fault_level'])))
        elif topic == 'factory/command/conveyor':
            state['running'] = bool(message['running'])
            node.conveyor(state['running'])
    client = connect('simulator',['factory/command/fault','factory/command/conveyor'],handler)
    rng = np.random.default_rng(CONFIG['simulator']['seed'])
    while True:
        event_id = str(uuid.uuid4())
        timestamp = utc_now()
        probability = .02+.085*state['level']
        class_id = int(rng.integers(0,3)) if rng.random() < probability else -1
        scene = random_scene(rng,class_id,'test',event_id)
        image,joint,sim_time = node.capture(scene)
        (DATA/'live').mkdir(parents=True,exist_ok=True)
        cv2.imwrite(str(DATA/'live/latest.jpg'),image)
        wave = vibration(state['level'],joint['velocity'][0],joint['position'][0],rng)
        rms = float(np.sqrt(np.mean(wave*wave)))
        sensor = {'event_id':event_id,'timestamp':timestamp,'sensor_id':'motor_01',
                  'vibration_x':rms,'vibration_y':.8*rms,'vibration_z':.65*rms,
                  'temperature':35+2*state['level']+float(rng.normal(0,.3)),
                  'fault_level':state['level'],'rpm':abs(joint['velocity'][0])*60/(2*np.pi),
                  'sampling_hz':2048,'waveform':wave.tolist(),'joint_observation':joint,'sim_time':sim_time}
        publish(client,'factory/sensor',sensor)
        if state['running']:
            _, encoded = cv2.imencode('.jpg',image)
            publish(client,'factory/image',{'event_id':event_id,'timestamp':timestamp,'image_base64':base64.b64encode(encoded).decode(),
                    'sim_time':sim_time,'ground_truth_class':class_id,'defect_probability':probability})
        publish(client,'factory/line',{'timestamp':timestamp,'fault_level':state['level'],'running':state['running'],
                'roller_velocity':joint['velocity'][1],'motor_rpm':sensor['rpm'],'defect_probability':probability},retain=True)
        logging.info('Fault_Level=%d vibration=%.3f defect_probability=%.3f conveyor=%s roller=%.4f',
                     state['level'],rms,probability,state['running'],joint['velocity'][1])
        time.sleep(CONFIG['simulator']['publication_interval_s'])


def main():
    """Start the ROS2 acquisition loop and select live or dataset-generation mode."""
    rclpy.init()
    node = Acquisition()
    threading.Thread(target=rclpy.spin,args=(node,),daemon=True).start()
    if os.environ.get('GENERATE_DATA') == '1':
        generate(node)
    else:
        live(node)


if __name__ == '__main__':
    main()
