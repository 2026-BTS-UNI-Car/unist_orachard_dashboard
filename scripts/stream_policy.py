"""Preview-only network budgets; source ROS messages and recordings are unchanged."""
import math
import asyncio
import json
import time

PROFILES = {
    "tiny": (160, 90, 2, 30, 3000),
    "slow": (320, 180, 4, 40, 30000),
    "balanced": (640, 360, 10, 55, 120000),
    "detail": (1280, 720, 15, 70, 400000),
}


def fit_size(width, height, max_width, max_height):
    ratio = min(1.0, max_width / width, max_height / height)
    return max(1, round(width * ratio)), max(1, round(height * ratio))


async def wait_for_ack(websocket, predicate, timeout=2.0):
    """One absolute deadline; obsolete/malformed ACKs never extend it."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        raw = await asyncio.wait_for(websocket.recv(), remaining)
        if not isinstance(raw, str):
            continue
        try:
            reply = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(reply, dict) and predicate(reply):
            return


async def pace_stream(interval, started):
    """Only the unused portion of a period remains after send/ACK latency."""
    delay = max(0.0, interval - (time.monotonic() - started))
    await asyncio.sleep(delay)
    return delay


class StreamBudget:
    def __init__(self, profile="auto", local_camera_30fps=False):
        self.profile = profile if profile in {*PROFILES, "auto"} else "auto"
        self.level = 0
        self.fast_count = 0
        self.local_camera_30fps = local_camera_30fps

    @property
    def values(self):
        name = ("tiny", "slow", "balanced", "detail")[self.level] if self.profile == "auto" else self.profile
        if name == "detail" and self.local_camera_30fps:
            # Explicit local simulation policy only. Internet/Jetson defaults
            # retain their original rate, quality and bandwidth limits.
            return (1280, 720, 30, 70, 5000000)
        return PROFILES[name]

    def acknowledge(self, elapsed):
        if self.profile != "auto":
            return
        if not math.isfinite(elapsed) or elapsed > 0.4:
            self.level = max(0, self.level - 1)
            self.fast_count = 0
        elif elapsed < 0.12:
            self.fast_count += 1
            if self.fast_count >= 15:
                self.level = min(3, self.level + 1)
                self.fast_count = 0
        else:
            self.fast_count = 0

    def delay(self, frame_bytes, fps):
        return max(1.0 / max(1, fps), frame_bytes / self.values[4])
