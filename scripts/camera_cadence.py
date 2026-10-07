"""Monotonic camera acquisition slots; missed slots never become a work queue."""
from __future__ import annotations

from collections import deque
import math


class CameraCadence:
    """Consume a slot before a potentially blocking read, not after publishing.

    A sensor read and JPEG work may overlap the nominal frame interval. Waiting
    another full interval after that work silently lowers acquisition rate.
    Absolute slots preserve the requested rate while skipping elapsed slots
    when processing is genuinely too slow; no duplicate/catch-up frames exist.
    All arguments are monotonic seconds. ROS header stamps remain independent.
    """

    def __init__(self, fps: float):
        if not math.isfinite(fps) or fps < 1:
            raise ValueError("camera_fps_must_be_finite_and_at_least_one")
        self.fps = float(fps)
        self.period = 1.0 / self.fps
        self.next_at: float | None = None
        self.attempts = 0
        self.skipped_slots = 0
        self.successful_reads = 0
        self.completed_at: deque[float] = deque(maxlen=120)

    def claim(self, now: float) -> bool:
        if not math.isfinite(now):
            raise ValueError("camera_clock_must_be_finite")
        if self.next_at is None:
            self.next_at = now
        if now + 1e-9 < self.next_at:
            return False
        missed = max(0, int(math.floor((now - self.next_at + 1e-9) / self.period)))
        self.skipped_slots += missed
        self.next_at += (missed + 1) * self.period
        self.attempts += 1
        return True

    def succeeded(self, completed_at: float) -> None:
        if not math.isfinite(completed_at):
            raise ValueError("camera_clock_must_be_finite")
        self.successful_reads += 1
        self.completed_at.append(completed_at)

    def status(self) -> dict:
        window = self.completed_at[-1] - self.completed_at[0] if len(self.completed_at) > 1 else 0.0
        return {
            "policy": "monotonic_acquisition_slots_skip_missed",
            "requested_fps": self.fps,
            "read_attempts": self.attempts,
            "successful_reads": self.successful_reads,
            "skipped_slots": self.skipped_slots,
            "observed_capture_hz": round((len(self.completed_at) - 1) / window, 3) if window > 0 else None,
            "observed_window_s": round(window, 3),
        }
