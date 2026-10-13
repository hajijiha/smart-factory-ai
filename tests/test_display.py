"""Bounded Xvfb startup readiness, independent of a native display or ROS."""
import pytest
from simulator.display import wait_for_display


class Clock:
    """Advance a deterministic startup deadline without blocking the tests."""

    def __init__(self):
        """Begin before the X server has reported a ready display."""
        self.value = 0.

    def now(self):
        """Read elapsed monotonic time."""
        return self.value

    def sleep(self, duration):
        """Advance elapsed time in place of an actual wait."""
        self.value += duration


def test_ready_display_accepts_any_allocated_number_from_live_xvfb(tmp_path):
    """A ready display can differ from stale display99 without removing any lock."""
    path = tmp_path/'ready'
    path.write_text('101\n', encoding='ascii')
    calls = []

    def alive(pid):
        """Require the specific launched Xvfb PID before and after its ready response."""
        calls.append(pid)
        return True

    assert wait_for_display(path, 1234, alive=alive) == ':101'
    assert calls == [1234, 1234]


def test_partial_display_number_cannot_start_gazebo(tmp_path):
    """A file created before Xvfb readiness must not be confused with a ready server."""
    path = tmp_path/'ready'
    path.write_text('9', encoding='ascii')
    clock = Clock()
    with pytest.raises(TimeoutError):
        wait_for_display(path, 12, .15, alive=lambda pid: True,
                         clock=clock.now, sleep=clock.sleep)
    assert clock.value >= .15


def test_failed_xvfb_cannot_reuse_ready_file(tmp_path):
    """Even a valid display number cannot authorize startup after that Xvfb exits."""
    path = tmp_path/'ready'
    path.write_text('99\n', encoding='ascii')
    with pytest.raises(RuntimeError, match='exited before'):
        wait_for_display(path, 12, alive=lambda pid: False)


def test_xvfb_exiting_after_ready_response_is_rejected(tmp_path):
    """A server dying between file readiness and final PID check cannot start Gazebo."""
    path = tmp_path/'ready'
    path.write_text('0\n', encoding='ascii')
    checks = iter([True, False])
    with pytest.raises(RuntimeError, match='exited after'):
        wait_for_display(path, 12, alive=lambda pid: next(checks))


def test_delayed_ready_response_waits_for_complete_server_message(tmp_path):
    """Startup waits for the server to finish initializing within the bounded deadline."""
    path = tmp_path/'ready'
    clock = Clock()

    def sleep(duration):
        """Represent Xvfb writing readiness only after its startup delay."""
        clock.sleep(duration)
        if clock.value >= .10:
            path.write_text('2\n', encoding='ascii')

    assert wait_for_display(path, 12, .30, alive=lambda pid: True,
                            clock=clock.now, sleep=sleep) == ':2'
    assert .10 <= clock.value < .30


def test_missing_ready_file_has_a_finite_deadline(tmp_path):
    """A hung live Xvfb cannot keep startup waiting indefinitely."""
    clock = Clock()
    with pytest.raises(TimeoutError):
        wait_for_display(tmp_path/'missing', 12, .15, alive=lambda pid: True,
                         clock=clock.now, sleep=clock.sleep)
    assert .15 <= clock.value <= .20


@pytest.mark.parametrize('pid,timeout', [(0, 10.), (-1, 10.), (12, 0.),
                                         (12, float('inf')), (12, float('nan'))])
def test_invalid_pid_and_unbounded_deadline_rejected(tmp_path, pid, timeout):
    """An accidental process-group PID or infinite wait cannot weaken startup guards."""
    with pytest.raises(ValueError):
        wait_for_display(tmp_path/'ready', pid, timeout, alive=lambda pid: True)


@pytest.mark.parametrize('value', ['-1\n', '99\nignored\n', '65536\n', ':99\n'])
def test_malformed_ready_display_rejected(tmp_path, value):
    """Only a numeric local display emitted by Xvfb can become the DISPLAY value."""
    path = tmp_path/'ready'
    path.write_text(value, encoding='ascii')
    with pytest.raises(ValueError):
        wait_for_display(path, 12, alive=lambda pid: True)
