"""V21 exact ancestry, flags, policies and constructor routing; no ROS/process/sensor."""
import ast
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_optimized_package as admission
import test_receiver_nearest_integration as receiver_tests
import test_final_scalar_features as final_tests
import test_receiver_end_features as end_tests
import test_preparation_features as preparation_tests
import test_commit_initial_features as initial_tests
import test_runner_features as wiring

runner = admission.runner
V20_REV = 'native_v20_owned_original_CDR_projection_reuse'
V20_PARENT = '5ab6b3d1339521d41a21dde76c97f031c23386e499f00e8fd4f41a4ab7baedf6'
REV = 'native_v21_owned_registration_imu_gap_scan_continuity_wait'
PARENT = 'e62315334c1accf2b4cd8c129f08694597ee787a28fd270a042a94466509860b'  # installed v20 HANDOFF
NEW = runner.V21_REANCHOR_FEATURES
POLICIES = dict(runner.V21_REANCHOR_POLICIES)
INGRESS = ('positive_age_imu_gap_reanchor_enabled', 'scan_continuity_reanchor_enabled')
FACTORY = 'registration_rejection_reanchor_enabled'
PREPARATION = preparation_tests.KEY


class V21LoaderTests(unittest.TestCase):
    setUp = admission.OptimizedPackageTests.setUp
    write = admission.OptimizedPackageTests.write
    load = admission.OptimizedPackageTests.load
    v9 = admission.OptimizedPackageTests.v9
    v10 = admission.OptimizedPackageTests.v10
    receiver = receiver_tests.CombinedAdmissionTests.receiver
    final = final_tests.FinalAdmissionTests.final
    end = end_tests.ReceiverEndLoaderTests.end
    preparation = preparation_tests.PreparationLoaderTests.preparation
    initial = initial_tests.CommitInitialLoaderTests.initial

    def v20(self):
        self.initial()
        self.manifest.update(source_revision=V20_REV, parent_manifest_sha256=V20_PARENT,
            positive_age_expiry_policy='typed_owned_WAIT_same_sample_SOURCE_RAW_no_TTL_change',
            original_callback_expiry_policy='typed_IMU_WAIT_admission_and_retention_same_sample_original_clocks',
            full_original_SHA_decode_at_every_association_preserved=False,
            owned_original_CDR_projection_reuse_preserved=True,
            full_original_SHA_envelope_digest_at_every_association_preserved=True,
            original_strict_decode_on_each_issue_preserved=True)

    def v21(self):
        self.v20()
        self.manifest.update(source_revision=REV, parent_manifest_sha256=PARENT)
        self.manifest.update(dict.fromkeys(NEW, True))
        self.manifest.update(POLICIES)

    def expected(self, *, new):
        value = dict(dict.fromkeys(admission.ALL_FEATURES, True), **{admission.NEAREST_FEATURE: False, PREPARATION: True})
        if new:
            value.update(dict.fromkeys(NEW, True))
        return value

    def test_v20_fixture_loads_without_v21_features(self):
        self.v20(); flags = {}
        self.assertIs(self.load(flags), True)
        self.assertEqual(flags, self.expected(new=False))

    def test_v21_exact_parent_publishes_eleven_features(self):
        self.v21(); flags = {}
        self.assertIs(self.load(flags), True)
        self.assertEqual(flags, self.expected(new=True))
        self.assertEqual(len(flags), 11)
        self.assertNotIn('physical_factory', sys.modules)
        self.assertNotIn('rclpy', sys.modules)

    def test_each_new_flag_and_policy_is_exact_before_any_publication(self):
        self.v21(); baseline = copy.deepcopy(self.manifest)
        cases = [(key, value) for key in NEW for value in (False, 0, 1, None, 'true')]
        cases += [(key, value) for key in POLICIES for value in (None, True, '', POLICIES[key] + '_relaxed')]
        for key, value in cases:
            self.manifest = copy.deepcopy(baseline); self.manifest[key] = value; flags = {}
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'reviewed_v21_reanchor'):
                self.load(flags)
            self.assertEqual(flags, {})
        for key in NEW + tuple(POLICIES):
            self.manifest = copy.deepcopy(baseline); del self.manifest[key]; flags = {}
            with self.subTest(missing=key), self.assertRaisesRegex(ValueError, 'reviewed_v21_reanchor'):
                self.load(flags)
            self.assertEqual(flags, {})

    def test_v21_keeps_every_cumulative_contract_and_exact_ancestry(self):
        self.v21(); baseline = copy.deepcopy(self.manifest)
        cases = (('parent_manifest_sha256', V20_PARENT), ('parent_manifest_sha256', '0' * 64),
                 ('source_revision', 'native_v21_unreviewed'), ('source_revision', True),
                 ('positive_age_expiry_policy', 'relaxed'), ('original_callback_expiry_policy', None),
                 ('full_original_SHA_decode_at_every_association_preserved', True),
                 ('owned_original_CDR_projection_reuse_preserved', False),
                 ('commit_initial_causal_raw_expiry_wait_enabled', False), (PREPARATION, False),
                 ('receiver_end_scalar_causal_raw_expiry_wait_enabled', 0), (admission.FINAL_FEATURE, None),
                 (admission.NEAREST_FEATURE, True), ('imu_gap_reanchor_enabled', 1),
                 ('selected_source_TTL_ns', 250000001), ('selected_callback_RAW_TTL_ns', 200000001),
                 ('initial_IMU_horizon_ns', 500000001), ('runtime_authority', True),
                 ('preserved_timing_hooks', False))
        for key, value in cases:
            self.manifest = copy.deepcopy(baseline); self.manifest[key] = value; flags = {}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.load(flags)
            self.assertEqual(flags, {})

    def test_older_revisions_cannot_activate_v21_flags_from_extra_metadata(self):
        for make in (self.v9, self.v10, self.receiver, self.final, self.end, self.preparation,
                     self.initial, self.v20):
            with self.subTest(make=make.__name__):
                self.setUp(); make()
                self.manifest.update(dict.fromkeys(NEW, True)); self.manifest.update(POLICIES); flags = {}
                with patch.dict('os.environ', {'NATIVE_V21_REANCHOR': 'true'}):
                    self.assertIs(self.load(flags), True)
                for key in NEW:
                    self.assertNotIn(key, flags)

    def test_late_source_corruption_or_import_collision_never_publishes(self):
        self.v21(); digest = self.write(); flags = {}; before = sys.path.copy()
        target = self.root / 'physical_factory.py'; saved = target.read_bytes(); target.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            runner.load_physical_package(self.root, digest, reviewed_features=flags)
        self.assertEqual(flags, {}); self.assertEqual(sys.path, before)
        target.write_bytes(saved)
        with patch.dict(sys.modules, {'physical_ingress': object()}), self.assertRaisesRegex(ValueError, 'isolated_physical'):
            runner.load_physical_package(self.root, digest, reviewed_features=flags)
        self.assertEqual(flags, {}); self.assertEqual(sys.path, before)


class V21WiringTests(unittest.TestCase):
    adapter = wiring.TrustedWiringTests.adapter

    def test_ingress_flags_reach_receiver_and_factory_flag_reaches_factory_only(self):
        calls = self.adapter(dict.fromkeys(NEW, True))
        self.assertEqual({key: calls[0][1][key] for key in INGRESS}, dict.fromkeys(INGRESS, True))
        self.assertNotIn(FACTORY, calls[0][1])
        self.assertEqual(calls[1], ('reset',))
        self.assertEqual(calls[2], ('factory', {'exact_surface_cache_enabled': True, FACTORY: True}))
        everything = dict(dict.fromkeys(wiring.ALL_FEATURES + (PREPARATION,) + NEW, True))
        calls = self.adapter(everything)
        self.assertEqual(set(calls[0][1]) & set(everything), set(wiring.FEATURES + INGRESS))
        self.assertTrue(set(calls[2][1]).isdisjoint(wiring.FEATURES + INGRESS))
        self.assertIs(calls[2][1][FACTORY], True)

    def test_false_v21_flags_are_omitted_for_legacy_constructors(self):
        for features in (None, {}, dict.fromkeys(NEW, False)):
            calls = self.adapter(features, legacy=True, cache=False)
            self.assertEqual(calls[0], ('legacy_receiver',))
            self.assertEqual(calls[2], ('factory', {}))

    def test_alias_or_unknown_override_is_rejected(self):
        for features in ({FACTORY: 1}, {INGRESS[0]: 'true'}, {INGRESS[1]: None},
                         {'registration_RPC_override': True}, {'scan_continuity': True}):
            with self.subTest(features=features), self.assertRaisesRegex(ValueError, 'verified_physical_receiver_features'):
                self.adapter(features)

    def test_no_cli_environment_or_RPC_activation_path(self):
        for module in (wiring.runner, wiring.service):
            tree = ast.parse(Path(module.__file__).read_bytes())
            arguments = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and
                         isinstance(n.func, ast.Attribute) and n.func.attr == 'add_argument']
            self.assertFalse(any(key in str(getattr(n.args[0], 'value', '')) for n in arguments for key in NEW))
        text = Path(wiring.service.__file__).read_text(encoding='utf-8')
        for key in NEW + tuple(POLICIES):
            self.assertNotIn(key, text)


if __name__ == '__main__':
    unittest.main()
