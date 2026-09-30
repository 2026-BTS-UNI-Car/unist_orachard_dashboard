"""Recorder API liveness must not be inferred from event-written files."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from dashboard_server.telemetry import TelemetryProvider


class RecordingStatusFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.provider = TelemetryProvider.__new__(TelemetryProvider)
        self.provider.data_recorder_status_file = Path(self.temp.name) / 'status.json'
        self.provider.recorder_api_url = 'http://127.0.0.1:19999'
        self.provider.recording_archive_dir = Path(self.temp.name) / 'recordings'
        self.provider._recording_archive_count = Mock(return_value=32)
        self.provider._recorder_get = Mock(return_value={'ok': False, 'error': 'offline'})

    def saved(self, **changes):
        value = dict(ok=True, active=True, state='recording', recording_id='old',
                     duration_s=1., samples={'camera_frames': 3}, updated_at=100.)
        value.update(changes)
        self.provider.data_recorder_status_file.write_text(json.dumps(value))
        return value

    def test_live_api_progress_and_stop_override_any_event_snapshot(self):
        self.saved()
        self.provider._recorder_get.side_effect = [
            dict(ok=True, active=True, state='recording', duration_s=2., samples={'camera_frames': 60}),
            dict(ok=True, active=True, state='recording', duration_s=3., samples={'camera_frames': 90}),
            dict(ok=True, active=False, state='idle', duration_s=None)]
        a, b, c = [self.provider.recording_status() for _ in range(3)]
        self.assertLess(a['duration_s'], b['duration_s'])
        self.assertLess(a['samples']['camera_frames'], b['samples']['camera_frames'])
        self.assertFalse(c['active'])
        self.assertTrue(all(row['status_fresh'] for row in (a, b, c)))
        self.assertTrue(all(row['status_source'] == 'recorder_api' for row in (a, b, c)))

    def test_recent_recording_or_idle_event_is_never_live_after_disconnect(self):
        for active in (True, False):
            original = self.saved(active=active, state='recording' if active else 'idle')
            with patch('dashboard_server.telemetry.time.time', return_value=100.01):
                result = self.provider.recording_status()
            self.assertFalse(result['ok']); self.assertFalse(result['status_fresh'])
            self.assertIsNone(result['active']); self.assertEqual(result['state'], 'unknown')
            self.assertEqual(result['last_known'], original)
            self.assertNotIn('duration_s', result); self.assertNotIn('samples', result)
            self.assertEqual(result['archive_count'], 32)
            self.assertEqual(json.loads(self.provider.data_recorder_status_file.read_text()), original)

    def test_absent_file_is_unknown_and_reconnect_supplies_fresh_status(self):
        result = self.provider.recording_status()
        self.assertFalse(result['ok']); self.assertIsNone(result['active'])
        self.provider._recorder_get.return_value = dict(ok=True, active=False, state='idle')
        self.assertTrue(self.provider.recording_status()['status_fresh'])

    def test_bad_or_future_file_time_does_not_invent_age_or_liveness(self):
        for timestamp in (None, '100', True, float('nan'), float('inf'), 101.):
            self.saved(updated_at=timestamp)
            with patch('dashboard_server.telemetry.time.time', return_value=100.):
                result = self.provider.recording_status()
            self.assertIsNone(result['last_known_age_s'])
            self.assertFalse(result['status_fresh'])


if __name__ == '__main__': unittest.main()
