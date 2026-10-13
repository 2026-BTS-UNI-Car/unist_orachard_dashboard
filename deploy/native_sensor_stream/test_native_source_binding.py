"""Source admission checks for the actual service, without ROS or device startup."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / 'optimized-package'), str(ROOT.parents[1])]
import stream_runner


class ServiceSourceBinding(unittest.TestCase):
    def test_service_map_digest_admits_the_actual_source_tree(self):
        tree = ast.parse((ROOT / 'native_observation_service.py').read_text(encoding='utf-8'))
        pins = [node.value.value for node in ast.walk(tree)
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and any(isinstance(t, ast.Attribute) and t.attr == 'native_map_manifest_sha256'
                        for t in node.targets)]
        self.assertEqual(len(pins), 1)
        self.assertTrue(stream_runner.load_native_map_sources(ROOT, pins[0]))

    def test_changed_runtime_file_is_rejected_even_with_the_original_manifest(self):
        manifest = ROOT / 'NATIVE_MAP_SOURCE.json'
        spec = json.loads(manifest.read_bytes())
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / manifest.name).write_bytes(manifest.read_bytes())
            for name in spec['files_sha256']:
                (target / name).write_bytes((ROOT / name).read_bytes())
            changed = target / 'committed_native_map.py'
            changed.write_bytes(changed.read_bytes() + b'\n# changed after release review\n')
            # Admission does not import these modules; isolate the digest check
            # from modules an earlier test may already have loaded from ROOT.
            with patch.dict(sys.modules, {Path(name).stem: None for name in spec['files_sha256']}):
                with self.assertRaisesRegex(ValueError, 'native_map_module_SHA_required'):
                    stream_runner.load_native_map_sources(target, hashlib.sha256(manifest.read_bytes()).hexdigest())

    def test_changing_the_pinned_parent_cannot_be_admitted_by_rehashing(self):
        spec = json.loads((ROOT / 'NATIVE_MAP_SOURCE.json').read_bytes())
        spec['parent_map_source_sha256'] = '0' * 64
        raw = json.dumps(spec).encode('utf-8')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / 'NATIVE_MAP_SOURCE.json').write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'exact_unqualified_native_map_sources_required'):
                stream_runner.load_native_map_sources(target, hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    unittest.main()
