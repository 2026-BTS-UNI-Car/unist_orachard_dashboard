"""Display clock correction only. Never changes sensor/playback timestamps."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path


def timestamp(value):
    try:
        date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return date.timestamp() if date.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def display_clock(path, metadata):
    original = metadata.get('started_at')
    result = dict(display_started_at=original, clock_quality='host_clock', clock_offset_s=0)
    try:
        file = Path(path) / 'recording_clock.json'
        if not file.is_file() or file.is_symlink() or file.stat().st_size > 16384:
            return result
        value = json.loads(file.read_text(encoding='utf-8'))
        offset = value.get('offset_s')
        corrected = value.get('display_started_at')
        first, second = timestamp(original), timestamp(corrected)
        if (value.get('schema') != 'ubts.recording_clock.v1'
                or value.get('original_started_at') != original
                or isinstance(offset, bool) or not isinstance(offset, (float, int))
                or not math.isfinite(offset) or abs(offset) > 86400
                or first is None or second is None or abs(second-first-offset) > .01):
            return result
        return dict(display_started_at=corrected, clock_quality='gnss_estimated',
                    clock_offset_s=offset, clock_evidence=value.get('evidence', {}))
    except (OSError, ValueError, TypeError):
        return result


def sort_key(row):
    return (timestamp(row.get('display_started_at')) or timestamp(row.get('started_at')) or 0,
            str(row.get('id', '')))
