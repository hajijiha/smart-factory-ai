"""Wait for Xvfb's dynamically allocated, ready display without deleting shared locks."""
import argparse
import math
import os
from pathlib import Path
import re
import time


def process_alive(pid):
    """Check that the X server process launched by this startup is still running."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_for_display(path, pid, timeout=10., alive=process_alive,
                     clock=time.monotonic, sleep=time.sleep):
    """Accept only a complete ready-display number from a live Xvfb before the deadline."""
    if type(pid) is not int or pid <= 0:
        raise ValueError('Xvfb requires the positive PID from this startup')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Xvfb readiness requires a finite positive deadline')
    path = Path(path)
    deadline = clock()+timeout
    while clock() < deadline:
        if not alive(pid):
            raise RuntimeError('Xvfb exited before display readiness; inspect /tmp/xvfb.log')
        value = path.read_text(encoding='ascii') if path.exists() else ''
        # -displayfd writes the newline only after Xvfb can accept connections.
        if value.endswith('\n'):
            if not re.fullmatch(r'[0-9]+\n', value):
                raise ValueError('Xvfb returned an invalid ready-display number')
            display = int(value.strip())
            if not 0 <= display <= 65535:
                raise ValueError('Xvfb returned an out-of-range display number')
            if not alive(pid):
                raise RuntimeError('Xvfb exited after reporting readiness')
            return ':'+str(display)
        sleep(.05)
    raise TimeoutError('Xvfb display was not ready within the startup deadline')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=Path)
    parser.add_argument('pid', type=int)
    parser.add_argument('--timeout', type=float, default=10.)
    arguments = parser.parse_args()
    print(wait_for_display(arguments.path, arguments.pid, arguments.timeout))
