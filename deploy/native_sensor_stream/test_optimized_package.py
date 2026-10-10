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

FEATURES = ("imu_gap_reanchor_enabled", "startup_expiry_discard_enabled", "current_expiry_reanchor_enabled")
FACTORY_FEATURE = "postconsumed_causal_raw_expiry_reanchor_enabled"
NEAREST_FEATURE = "strict_nearest_gates_enabled"
RECEIVER_FEATURE = "receiver_commit_causal_raw_expiry_reanchor_enabled"
FINAL_FEATURE = "final_scalar_causal_raw_expiry_reanchor_enabled"
ALL_FEATURES = FEATURES + (FACTORY_FEATURE, NEAREST_FEATURE, RECEIVER_FEATURE, FINAL_FEATURE)

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

    def load(self, features=None):
        digest = self.write()
        with patch.object(sys, 'path', sys.path.copy()):
            return runner.load_physical_package(self.root, digest, reviewed_features=features)

    def test_reviewed_v4_enables_only_trusted_constructor_feature(self):
        self.assertIs(self.load(), True)
        self.assertNotIn('physical_factory', sys.modules)
        self.assertNotIn('rclpy', sys.modules)

    def v6(self):
        self.manifest.update(source_revision='native_v6_exact_cache_admission_timing',
            parent_manifest_sha256='4d65ac4cc51172e83b8242bc074c9d6f222bc672cf552691c1bc89b70a15d1c5',
            admission_timing_only=True)

    def v7(self):
        self.v6()
        self.manifest.update(source_revision='native_v7_exact_cache_imu_gap_timing',
            parent_manifest_sha256='f372e8ed6f1d81e5dc54990a51c435ffa9e83a65d80a22e3f3d23bc06c50d9ad',
            imu_gap_timing_only=True)

    def test_reviewed_v7_is_diagnostic_only_and_preserves_trusted_cache(self):
        self.v7()
        self.assertIs(self.load(), True)
        self.assertNotIn('physical_factory', sys.modules)
        self.assertNotIn('rclpy', sys.modules)

    def test_v7_requires_both_diagnostic_flags_as_exact_boolean_true(self):
        self.v7()
        original = copy.deepcopy(self.manifest)
        for key in ('admission_timing_only', 'imu_gap_timing_only'):
            for value in (False, 0, 1, None, 'true'):
                self.manifest = copy.deepcopy(original)
                self.manifest[key] = value
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'timing_required'):
                    self.load()
            self.manifest = copy.deepcopy(original)
            del self.manifest[key]
            with self.assertRaisesRegex(ValueError, 'timing_required'):
                self.load()

    def test_v7_rejects_each_prior_parent_and_unreviewed_revision(self):
        self.v7()
        original = copy.deepcopy(self.manifest)
        for parent in ('0'*64, '4d65ac4cc51172e83b8242bc074c9d6f222bc672cf552691c1bc89b70a15d1c5',
                       'fb57299a084f4b16a90abb31a627048b4a8b11175bb260089cd0e69ae0aa1ed3'):
            self.manifest = copy.deepcopy(original)
            self.manifest['parent_manifest_sha256'] = parent
            with self.subTest(parent=parent), self.assertRaisesRegex(ValueError, 'source_contract'):
                self.load()
        self.manifest = copy.deepcopy(original)
        self.manifest['source_revision'] = 'native_v8_unreviewed'
        with self.assertRaisesRegex(ValueError, 'source_contract'):
            self.load()

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

    def test_real_frozen_final_scalar_package_validates_without_importing_sensor_or_ROS_modules(self):
        package = Path(__file__).resolve().with_name('optimized-package')
        expected = '78ba87427acbd534de90bbca68e960bd0335cb82a15e352b690aa9ca2e821b1e'
        self.assertEqual(runner.sha_file(package / 'HANDOFF.json'), expected)
        with patch.object(sys, 'path', sys.path.copy()):
            features = {}
            self.assertIs(runner.load_physical_package(package, expected, reviewed_features=features), True)
            self.assertEqual(features, dict(dict.fromkeys(FEATURES+(FACTORY_FEATURE,RECEIVER_FEATURE,FINAL_FEATURE), True), **{NEAREST_FEATURE:False,'preparation_causal_raw_expiry_reanchor_enabled':True}))
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


    def v9(self):
        self.manifest.update(source_revision='native_v9_exact_cache_positive_scan_expiry_wait',
            parent_manifest_sha256='92303f90fe9efc98db14aa77ced6f37c990f0f24195395f7988c5dd0f0f51df3',
            admission_timing_only=False, imu_gap_timing_only=False, preserved_timing_hooks=True)
        self.manifest.update(dict.fromkeys(FEATURES, True))

    def test_v9_features_populated_only_after_verified_closure_and_bool_return_preserved(self):
        self.v9()
        features = {}
        self.assertIs(self.load(features), True)
        self.assertEqual(features, dict(dict.fromkeys(FEATURES, True), **{FACTORY_FEATURE:False,NEAREST_FEATURE:False,RECEIVER_FEATURE:False,FINAL_FEATURE:False}))
        self.assertIs(self.load(), True)

    def test_v9_all_three_features_require_literal_true_not_missing_or_integer(self):
        self.v9()
        original = copy.deepcopy(self.manifest)
        for key in FEATURES:
            for value in (False, 0, 1, None, 'true'):
                self.manifest = copy.deepcopy(original)
                self.manifest[key] = value
                features = {}
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'recovery_feature_required'):
                    self.load(features)
                self.assertEqual(features, {})
            self.manifest = copy.deepcopy(original)
            del self.manifest[key]
            with self.assertRaisesRegex(ValueError, 'recovery_feature_required'):
                self.load({})

    def test_v9_recovery_timing_contract_has_exact_boolean_semantics(self):
        self.v9()
        original = copy.deepcopy(self.manifest)
        for key, bad_values in (
                ('admission_timing_only', (True, 0, 1, None, 'false')),
                ('imu_gap_timing_only', (True, 0, 1, None, 'false')),
                ('preserved_timing_hooks', (False, 0, 1, None, 'true'))):
            for value in bad_values:
                self.manifest = copy.deepcopy(original)
                self.manifest[key] = value
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'recovery_timing_contract'):
                    self.load({})
            self.manifest = copy.deepcopy(original)
            del self.manifest[key]
            with self.assertRaisesRegex(ValueError, 'recovery_timing_contract'):
                self.load({})

    def test_v9_wrong_parent_and_unreviewed_v8_do_not_populate_features(self):
        self.v9()
        original = copy.deepcopy(self.manifest)
        for key, value in (('parent_manifest_sha256', '0'*64),
                           ('parent_manifest_sha256', '1af864127cf4ab8c0f30f8142f93d32f16bd3d7a3145534008aafd2288b2b883'),
                           ('source_revision', 'native_v8_exact_cache_expected_imu_gap_wait'),
                           ('source_revision', True)):
            self.manifest = copy.deepcopy(original)
            self.manifest[key] = value
            features = {}
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'source_contract'):
                self.load(features)
            self.assertEqual(features, {})

    def test_old_v5_v6_v7_never_enable_manifest_supplied_recovery_features(self):
        original = copy.deepcopy(self.manifest)
        for revision in ('v5', 'v6', 'v7'):
            self.manifest = copy.deepcopy(original)
            if revision != 'v5':getattr(self, revision)()
            self.manifest.update(dict.fromkeys(FEATURES, True))
            features = {}
            self.assertIs(self.load(features), True)
            self.assertEqual(features, dict.fromkeys(ALL_FEATURES, False))

    def test_legacy_v3_populates_false_only_and_ignores_manifest_recovery_flags(self):
        for index in range(8):
            name = 'legacy-reference-' + str(index) + '.txt'
            (self.root/name).write_bytes(b'legacy-extra-not-runtime')
            self.manifest['files_sha256'][name] = runner.sha_file(self.root/name)
        self.manifest.update(schema='ubts.private.physical_native_shadow_factory.v3', owned_count=22)
        self.manifest.update(dict.fromkeys(FEATURES, True))
        features = {}
        self.assertIs(self.load(features), False)
        self.assertEqual(features, dict.fromkeys(ALL_FEATURES, False))

    def v10(self):
        self.v9()
        self.manifest.update(source_revision='native_v10_exact_cache_postconsumed_causal_raw_expiry_wait',
            parent_manifest_sha256='42e46534f700cacdce08ea9aee64b3fe9667f6bb35a297b99182507d1afd12a1')
        self.manifest[FACTORY_FEATURE]=True

    def test_v10_verified_factory_feature_separate_from_three_ingress_features(self):
        self.v10();features={}
        self.assertIs(self.load(features),True)
        self.assertEqual(features,dict(dict.fromkeys(FEATURES+(FACTORY_FEATURE,),True), **{NEAREST_FEATURE:False,RECEIVER_FEATURE:False,FINAL_FEATURE:False}))

    def test_v10_factory_feature_is_literal_true_not_missing_or_boolean_alias(self):
        self.v10();original=copy.deepcopy(self.manifest)
        for value in (False,0,1,None,'true'):
            self.manifest=copy.deepcopy(original);self.manifest[FACTORY_FEATURE]=value;features={}
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'consumed_RAW_expiry_feature_required'):
                self.load(features)
            self.assertEqual(features,{})
        self.manifest=copy.deepcopy(original);del self.manifest[FACTORY_FEATURE]
        with self.assertRaisesRegex(ValueError,'consumed_RAW_expiry_feature_required'):self.load({})

    def test_v10_wrong_parent_unknown_revision_and_nonboolean_flags_leave_output_empty(self):
        self.v10();original=copy.deepcopy(self.manifest)
        cases=(('parent_manifest_sha256','0'*64),('parent_manifest_sha256','92303f90fe9efc98db14aa77ced6f37c990f0f24195395f7988c5dd0f0f51df3'),
            ('source_revision','native_v11_unreviewed'),('source_revision',True),
            ('admission_timing_only',0),('imu_gap_timing_only',0),('preserved_timing_hooks',1),
            ('imu_gap_reanchor_enabled',1),('selected_callback_RAW_TTL_ns',True))
        for key,value in cases:
            self.manifest=copy.deepcopy(original);self.manifest[key]=value;features={}
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.load(features)
            self.assertEqual(features,{})

    def test_older_optimized_revisions_cannot_activate_new_factory_manifest_flag(self):
        original=copy.deepcopy(self.manifest)
        for revision in ('v5','v6','v7','v9'):
            self.manifest=copy.deepcopy(original)
            if revision!='v5':getattr(self,revision)()
            self.manifest[FACTORY_FEATURE]=True;features={}
            self.assertIs(self.load(features),True)
            self.assertIs(features[FACTORY_FEATURE],False)

    def test_v10_late_corruption_never_populates_factory_feature(self):
        self.v10();digest=self.write();features={};before=sys.path.copy()
        (self.root/'physical_factory.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'source_changed'):
            runner.load_physical_package(self.root,digest,reviewed_features=features)
        self.assertEqual(features,{});self.assertEqual(sys.path,before)

    def test_trusted_output_must_be_an_exact_empty_dict(self):
        class Derived(dict):pass
        self.v9()
        for features in ([], Derived(), {'imu_gap_reanchor_enabled': True}, {'untrusted': False}):
            before = copy.deepcopy(features)
            with self.subTest(features=features), self.assertRaisesRegex(ValueError, 'empty_trusted_reviewed_features'):
                self.load(features)
            self.assertEqual(features, before)

    def test_no_features_or_sys_path_mutation_on_late_file_or_isolation_failure(self):
        self.v9()
        digest = self.write()
        before = sys.path.copy()
        features = {}
        path = self.root/'physical_ingress.py'
        saved = path.read_bytes()
        path.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            runner.load_physical_package(self.root, digest, reviewed_features=features)
        self.assertEqual(features, {})
        self.assertEqual(sys.path, before)
        path.write_bytes(saved)
        with patch.dict(sys.modules, {'inputs': object()}), self.assertRaisesRegex(ValueError, 'isolated_physical_module_loader'):
            runner.load_physical_package(self.root, digest, reviewed_features=features)
        self.assertEqual(features, {})
        self.assertEqual(sys.path, before)


if __name__ == '__main__':
    unittest.main()
