"""Asynchronous MQTT vibration-window diagnosis worker."""
import logging
import queue
import time
from factory.common import connect, publish, ARTIFACTS
from factory.pdm.features import extract, NAMES
from factory.pdm.model import Detector


def main():
    """Wait for model readiness and consume independent sensor events."""
    while not (ARTIFACTS/'pdm.pt').exists():
        logging.info('Waiting for trained PdM checkpoint')
        time.sleep(5)
    detector = Detector(ARTIFACTS/'pdm.pt')
    events = queue.Queue(maxsize=256)
    def handler(topic, message):
        """Queue work without blocking MQTT keepalive."""
        events.put_nowait(message)
    client = connect('pdm', ['factory/sensor'], handler)
    previous = 'normal'
    while True:
        message = events.get()
        start = time.perf_counter()
        feature, spectrum = extract(message['waveform'], message['sampling_hz'], message['rpm']/60)
        result = detector.infer(feature)
        result.update(event_id=message['event_id'], timestamp=message['timestamp'],
                      features=dict(zip(NAMES, feature.tolist())), spectrum=spectrum,
                      inference_ms=1000*(time.perf_counter()-start))
        publish(client, 'factory/health', result)
        if result['status'] in ['warning', 'danger'] and result['status'] != previous:
            publish(client, 'factory/alarm', {'timestamp':message['timestamp'], 'event_id':message['event_id'],
                    'source':'pdm', 'severity':result['status'], 'message':f"설비 {result['status']} / HI {result['health_index']}"})
        previous = result['status']
        logging.info('HI %.2f status=%s CPU %.2fms', result['health_index'], result['status'], result['inference_ms'])


if __name__ == '__main__':
    main()
