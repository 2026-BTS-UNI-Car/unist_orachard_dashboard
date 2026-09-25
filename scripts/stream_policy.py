"""Preview-only network budgets; source ROS messages and recordings are unchanged."""
import math

PROFILES = {
    "tiny": (160, 90, 2, 30, 3000),
    "slow": (320, 180, 4, 40, 30000),
    "balanced": (640, 360, 10, 55, 120000),
    "detail": (1280, 720, 15, 70, 400000),
}


def fit_size(width, height, max_width, max_height):
    ratio = min(1.0, max_width / width, max_height / height)
    return max(1, round(width * ratio)), max(1, round(height * ratio))


class StreamBudget:
    def __init__(self, profile="auto"):
        self.profile = profile if profile in {*PROFILES, "auto"} else "auto"
        self.level = 0
        self.fast_count = 0

    @property
    def values(self):
        name = ("tiny", "slow", "balanced", "detail")[self.level] if self.profile == "auto" else self.profile
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
