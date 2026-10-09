"""Configuration, timestamps and resilient MQTT transport shared by services."""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import paho.mqtt.client as mqtt
import yaml

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
CONFIG = yaml.safe_load(Path(os.environ.get('FACTORY_CONFIG', '/app/config.yaml')).read_text())
DATA = Path(os.environ.get('DATA_DIR', CONFIG['paths']['data']))
ARTIFACTS = Path(os.environ.get('ARTIFACT_DIR', CONFIG['paths']['artifacts']))
REPORTS = Path(os.environ.get('REPORT_DIR', CONFIG['paths']['reports']))


def utc_now():
    """Return an unambiguous UTC ISO-8601 event timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def connect(client_id, topics=(), handler=None):
    """Start a reconnecting MQTT client; subscription is renewed after reconnect."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    def on_connect(client, userdata, flags, reason_code, properties):
        """Resubscribe to all configured topics on connection."""
        for topic in topics:
            client.subscribe(topic, qos=1)
        logging.info('%s MQTT connected: %s', client_id, reason_code)
    def on_message(client, userdata, message):
        """Decode JSON and isolate malformed messages from the network loop."""
        try:
            if handler:
                handler(message.topic, json.loads(message.payload))
        except Exception:
            logging.exception('%s rejected message on %s', client_id, message.topic)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(1, 20)
    client.connect_async(os.environ.get('MQTT_HOST', CONFIG['mqtt']['host']), int(os.environ.get('MQTT_PORT', CONFIG['mqtt']['port'])))
    client.loop_start()
    return client


def publish(client, topic, message, retain=False):
    """Publish a JSON envelope with QoS 1, rejecting NaN serialization."""
    return client.publish(topic, json.dumps(message, allow_nan=False), qos=1, retain=retain)
