"""Synthetic package admission tests. No ROS, subprocess or sensor owner."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('optimized_runner_tested', Path(__file__).with_name('stream_runner.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
assert Path(runner.__file__).resolve() == Path(__file__).with_name('stream_runner.py').resolve()

PATHS = (
    'inputs.py', 'VENDORED_INPUTS.json', 'physical_ingress.py', 'physical_factory.py', 'observer_rows.py',
    'common/__init__.py', 'common/native_ownership.py', 'common/original_cdr.py', 'common/sensor_contract.py',
    'common/sensor_views.py', 'kernel/__init__.py', 'kernel/fit_workers.py', 'kernel/raw_localization.py',
)


class OptimizedPackageTests(unittest.TestCase):
    def setUp(self):
        # Discovery also imports legacy row tests. Isolate only this synthetic
        # loader fixture; the runtime loader still rejects an imported package.
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        for name in ('physical_factory', 'physical_ingress', 'inputs'):
            sys.modules.pop(name, None)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {}
        for name in PATHS:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(('synthetic-loader-fixture:' + name).encode())
            self.files[name] = runner.sha_file(p)
        self.closure = dict(schema='ubts.private.physical_native_shadow_deployment_closure.v1',
                            count=13, files_sha256=self.files.copy())
        self.manifest = dict(schema='ubts.private.physical_native_shadow_factory.v4', owned_count=14,
            deployment_closure_count=13, files_sha256=self.files.copy(),
            parent_manifest_sha256='fb57299a084f4b16a90abb31a627048b4a8b11175bb260089cd0e69ae0aa1ed3',
            source_revision='native_v5_typed_envelope_seal_exact_surface_cache', source_frozen=True,
            runtime_authority=False, motor_transport=False, autonomy_output_enabled=False,
            physical_body_calibration_braking_REP145_qualified=False,
            installed_original_callback_or_live_pipeline_qualified=False,
            after_last_association_final_scalar_fence=True,
            full_original_SHA_decode_at_every_association_preserved=True, exact_surface_cache_enabled=True,
            selected_source_TTL_ns=250000000, selected_callback_RAW_TTL_ns=200000000,
            initial_IMU_horizon_ns=500000000)

    def write(self):
        (self.root / 'DEPLOY_CLOSURE.json').write_text(json.dumps(self.closure))
        self.manifest['files_sha256']['DEPLOY_CLOSURE.json'] = runner.sha_file(self.root / 'DEPLOY_CLOSURE.json')
        (self.root / 'HANDOFF.json').write_text(json.dumps(self.manifest))
        return runner.sha_file(self.root / 'HANDOFF.json')

    def load(self):
        digest = self.write()
        with patch.object(sys, 'path', sys.path.copy()):
            return runner.load_physical_package(self.root, digest)

    def test_reviewed_v4_enables_only_trusted_constructor_feature(self):
        self.assertIs(self.load(), True)
        self.assertNotIn('physical_factory', sys.modules)
        self.assertNotIn('rclpy', sys.modules)

    def v6(self):
        self.manifest.update(source_revision='native_v6_exact_cache_admission_timing',
            parent_manifest_sha256='4d65ac4cc51172e83b8242bc074c9d6f222bc672cf552691c1bc89b70a15d1c5',
            admission_timing_only=True)

    def test_reviewed_v6_admission_timing_preserves_only_verified_cache_feature(self):
        self.v6()
        self.assertIs(self.load(), True)
        self.assertNotIn('physical_factory', sys.modules)
        self.assertNotIn('rclpy', sys.modules)

    def test_v6_admission_timing_requires_exact_boolean_true(self):
        self.v6()
        original = copy.deepcopy(self.manifest)
        for value in (False, 0, 1, None, 'true'):
            self.manifest = copy.deepcopy(original)
            self.manifest['admission_timing_only'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'admission_timing_required'):
                self.load()
        self.manifest = copy.deepcopy(original)
        del self.manifest['admission_timing_only']
        with self.assertRaisesRegex(ValueError, 'admission_timing_required'):
            self.load()

    def test_revision_parent_pair_is_exact_and_unknown_or_boolean_revision_rejects(self):
        v5 = copy.deepcopy(self.manifest)
        self.v6()
        v6 = copy.deepcopy(self.manifest)
        for manifest, key, value in (
                (v5, 'parent_manifest_sha256', v6['parent_manifest_sha256']),
                (v6, 'parent_manifest_sha256', v5['parent_manifest_sha256']),
                (v6, 'parent_manifest_sha256', '0' * 64),
                (v6, 'source_revision', 'unreviewed'),
                (v6, 'source_revision', True),
                (v6, 'source_revision', False),
                (v6, 'source_revision', None)):
            self.manifest = copy.deepcopy(manifest)
            self.manifest[key] = value
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'source_contract'):
                self.load()

    def test_legacy_v3_still_returns_feature_false_with_original_22_owned_13_closure(self):
        for index in range(8):
            name = 'legacy-reference-' + str(index) + '.txt'
            path = self.root / name
            path.write_bytes(b'legacy-extra-not-runtime')
            self.manifest['files_sha256'][name] = runner.sha_file(path)
        self.manifest.update(schema='ubts.private.physical_native_shadow_factory.v3', owned_count=22)
        for key in ('source_revision', 'parent_manifest_sha256', 'source_frozen',
                    'full_original_SHA_decode_at_every_association_preserved',
                    'exact_surface_cache_enabled', 'selected_source_TTL_ns',
                    'selected_callback_RAW_TTL_ns', 'initial_IMU_horizon_ns'):
            del self.manifest[key]
        self.assertIs(self.load(), False)
        self.assertNotIn('physical_factory', sys.modules)

    def test_real_frozen_f372_package_validates_without_importing_sensor_or_ROS_modules(self):
        package = (Path(__file__).resolve().parents[4] /
                   'native-admission-timing-package-candidate-20261010' / 'package')
        expected = 'f372e8ed6f1d81e5dc54990a51c435ffa9e83a65d80a22e3f3d23bc06c50d9ad'
        self.assertEqual(runner.sha_file(package / 'HANDOFF.json'), expected)
        with patch.object(sys, 'path', sys.path.copy()):
            self.assertIs(runner.load_physical_package(package, expected), True)
            self.assertEqual(Path(sys.path[0]), package.resolve())
        self.assertNotIn('physical_factory', sys.modules)
        self.assertNotIn('physical_ingress', sys.modules)
        self.assertNotIn('rclpy', sys.modules)

    def test_all_authority_and_unverified_qualification_flags_reject_nonfalse(self):
        original = copy.deepcopy(self.manifest)
        for key in ('runtime_authority', 'motor_transport', 'autonomy_output_enabled',
                    'physical_body_calibration_braking_REP145_qualified',
                    'installed_original_callback_or_live_pipeline_qualified'):
            for value in (True, 0, None):
                with self.subTest(key=key, value=value):
                    self.manifest = copy.deepcopy(original)
                    self.manifest[key] = value
                    with self.assertRaisesRegex(ValueError, 'authority_or_qualification'):
                        self.load()

    def test_exact_limits_cannot_be_relaxed_or_boolean(self):
        original = copy.deepcopy(self.manifest)
        for key in ('selected_source_TTL_ns', 'selected_callback_RAW_TTL_ns', 'initial_IMU_horizon_ns'):
            for value in (self.manifest[key] + 1, True, float(self.manifest[key])):
                with self.subTest(key=key, value=value):
                    self.manifest = copy.deepcopy(original)
                    self.manifest[key] = value
                    with self.assertRaisesRegex(ValueError, 'original_time_limit'):
                        self.load()

    def test_all_required_preservation_and_cache_flags_are_strict_true(self):
        original = copy.deepcopy(self.manifest)
        for key in ('source_frozen', 'full_original_SHA_decode_at_every_association_preserved',
                    'exact_surface_cache_enabled', 'after_last_association_final_scalar_fence'):
            for value in (False, 1, None):
                self.manifest = copy.deepcopy(original)
                self.manifest[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.load()

    def test_parent_revision_schema_and_owned_paths_must_match(self):
        original = copy.deepcopy(self.manifest)
        for key, value in (('parent_manifest_sha256', '0'*64), ('source_revision', 'unreviewed'),
                           ('schema', 'ubts.private.physical_native_shadow_factory.v5'), ('owned_count', 13)):
            self.manifest = copy.deepcopy(original)
            self.manifest[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.load()
        self.manifest = copy.deepcopy(original)
        self.manifest['files_sha256']['../escape.py'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'source_contract'):
            self.load()

    def test_corrupted_selected_file_rejected_even_with_reviewed_handoff_hash(self):
        digest = self.write()
        (self.root / 'common/native_ownership.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            runner.load_physical_package(self.root, digest)

    def test_reviewed_handoff_hash_is_required_before_parsing_or_importing(self):
        self.write()
        with self.assertRaisesRegex(ValueError, 'manifest_required'):
            runner.load_physical_package(self.root, '0'*64)

    def test_existing_module_prevents_cross_package_reuse(self):
        digest = self.write()
        with patch.dict(sys.modules, {'physical_factory': object()}):
            with self.assertRaisesRegex(ValueError, 'isolated_physical_module_loader'):
                runner.load_physical_package(self.root, digest)

    def test_closure_cannot_substitute_or_add_unknown_file(self):
        self.closure['files_sha256']['unknown.py'] = self.closure['files_sha256'].pop('inputs.py')
        with self.assertRaisesRegex(ValueError, 'exact_optimized_physical_deployment_paths'):
            self.load()


if __name__ == '__main__':
    unittest.main()
