"""Recorded map windows: level-1 blob, LRU + single-flight cache (C1) and
separate window slot / deferred reads (C2, E2)."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zlib

from dashboard_server.integrated_archive import IndexedTileHistory, build, localization_fingerprint
from dashboard_server.mapping_operations import ManagedArchiveMapping, MappingDeferred
from dashboard_server.window_cells import compact_window
from tests.test_integrated_mapping import scan


def fixture(archive, scans=8, complete=True):
    """Small complete integrated history (python 3.10-safe timestamp)."""
    source = archive / 'mapping_source'; source.mkdir(parents=True)
    duration = scans * 0.1
    (archive / 'metadata.json').write_text(json.dumps(dict(started_at='1970-01-01T00:16:40+00:00', duration_s=duration)))
    with gzip.open(source / 'sensors.jsonl.gz', 'wt') as stream:
        for i in range(scans):
            stream.write(json.dumps(dict(topic='/scan', message=scan(1000. + i * .1), bag_time_ns=round((1000. + i * .1) * 1e9))) + '\n')
    sha = hashlib.sha256((source / 'sensors.jsonl.gz').read_bytes()).hexdigest()
    (source / 'manifest.json').write_text(json.dumps(dict(complete=True, archive_id=archive.name, output_sha256={'sensors.jsonl.gz': sha},
                                                          topic_counts={'/scan': scans})))
    corrected = {str(1000. + i * .1): dict(segment=0, frame='isolated', pose=[0.05 * i, 0, 0.01 * i]) for i in range(scans)}
    (source / 'trajectory.json').write_text(json.dumps(dict(source_sensor_sha256=sha, localization_fingerprint=localization_fingerprint(),
                                                            corrected=corrected)))
    build(archive, {}, lambda *a: None)
    file = archive / 'integrated_map_history.json'
    if not complete:
        history = json.loads(file.read_text()); history.update(complete=False, processed_t_s=duration / 2)
        file.write_text(json.dumps(history))
    return archive


class WindowCacheTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.archive = fixture(Path(self.root.name) / 'rec')

    def tearDown(self):
        self.root.cleanup()

    def test_blob_level_one_decodes_to_the_same_cells(self):
        reader = IndexedTileHistory()
        raw = reader._window(self.archive, 0.0, 1, None)
        fast = compact_window(raw, 'xor2-deflate-v1')
        blob = zlib.decompress(base64.b64decode(fast['cell_blob']))
        self.assertEqual(len(blob), fast['cell_blob_bytes'])
        # Same packed cells as the previous level-6 encoding would carry.
        self.assertEqual(blob, zlib.decompress(zlib.compress(blob, 6)))

    def test_second_identical_request_is_a_cache_hit_and_fast(self):
        reader = IndexedTileHistory()
        first = reader.window(self.archive, 0.3, 1, 'xor2-deflate-v1')
        started = time.perf_counter()
        second = reader.window(self.archive, 0.3, 1, 'xor2-deflate-v1')
        self.assertLess((time.perf_counter() - started) * 1000, 10)
        self.assertIs(second, first)
        self.assertEqual(reader.window_stats['hits'], 1)
        # start is rounded down to 0.5 s: the window still covers the request.
        self.assertLessEqual(first['start_s'], 0.3)
        self.assertIs(reader.window(self.archive, 0.12, 1, 'xor2-deflate-v1'), first)
        # Cached and freshly computed windows are identical.
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(reader._window(self.archive, 0.0, 1, 'xor2-deflate-v1'), sort_keys=True))

    def test_concurrent_requests_compute_once(self):
        reader = IndexedTileHistory()
        original = reader._window
        calls = []
        def slow(*args, **kwargs):
            calls.append(1); time.sleep(0.2)
            return original(*args, **kwargs)
        reader._window = slow
        results = []
        threads = [threading.Thread(target=lambda: results.append(reader.window(self.archive, 0.0, 1, 'xor2-deflate-v1'))) for _ in range(3)]
        for t in threads: t.start()
        for t in threads: t.join(5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r is results[0] for r in results))

    def test_memory_bound_and_entry_bound(self):
        reader = IndexedTileHistory()
        reader.WINDOW_CACHE_ENTRIES = 2
        for start in (0.0, 0.5, 1.0 - 0.5, 0.0):
            reader.window(self.archive, start, 1, None)
        self.assertLessEqual(len(reader.window_cache), 2)
        size = next(iter(reader.window_cache.values()))[1]
        reader.WINDOW_CACHE_BYTES = size * 4  # one entry fits (<= cap/4), two would not exceed the cap
        reader.window(self.archive, 0.0, 2, None); reader.window(self.archive, 0.0, 3, None)
        self.assertLessEqual(reader.window_cache_bytes, reader.WINDOW_CACHE_BYTES)

    def test_incomplete_or_changed_history_is_not_served_from_cache(self):
        reader = IndexedTileHistory()
        first = reader.window(self.archive, 0.0, 1, None)
        file = self.archive / 'integrated_map_history.json'
        history = json.loads(file.read_text()); history.update(complete=False, processed_t_s=0.3)
        time.sleep(0.01); file.write_text(json.dumps(history))
        second = reader.window(self.archive, 0.0, 1, None)
        self.assertIsNot(second, first)
        self.assertFalse(second['complete'])
        third = reader.window(self.archive, 0.0, 1, None)
        self.assertIsNot(third, second, 'an unfinished history is never cached')


class ManagedWindowTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.records = Path(self.root.name) / 'records'
        fixture(self.records / 'rec')
        self.mapping = ManagedArchiveMapping(self.records, None, start_monitor=False)

    def tearDown(self):
        self.mapping.close(); self.root.cleanup()

    def test_window_slot_is_separate_and_busy_is_deferred(self):
        self.mapping.read_wait_s = 0.2
        self.assertTrue(self.mapping.window_slot.acquire(timeout=1))
        try:
            started = time.monotonic()
            status = self.mapping.status('rec', '0.3')  # short reads still work
            self.assertTrue(status['ok'])
            with self.assertRaises(MappingDeferred) as error:
                self.mapping.window('rec', '0', '1')
            self.assertEqual(str(error.exception), 'mapping_read_capacity_busy')
            self.assertLess(time.monotonic() - started, 2)
        finally:
            self.mapping.window_slot.release()


if __name__ == '__main__':
    unittest.main()
