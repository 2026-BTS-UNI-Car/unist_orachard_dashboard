"""Combined reviewed closure routing; no ROS/process/sensor execution."""
import ast
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_optimized_package as admission
import test_runner_features as wiring

RECEIVER = 'receiver_commit_causal_raw_expiry_reanchor_enabled'
NN = 'strict_nearest_gates_enabled'
RECEIVER_REV = 'native_v11_exact_cache_receiver_commit_causal_raw_expiry_wait'
COMBINED_REV = 'native_v12_exact_cache_strict_nearest_receiver_commit_causal_raw_expiry_wait'
COMMON_PARENT = 'd24a38bafde135b70db87c50e39303f4d6d82c2267ffeef3f769a45587ab57f1'
RECEIVER_PARENT = '68bd1239327ca8701004fd284484223b1ddfabd9105ace933ee8ead9ec1ed5a7'
NN_SOURCE = 'a27cad5b3ae16bfac181199ccbc931b8f41192d2ded02039030b0cd0138d371c'


class CombinedAdmissionTests(unittest.TestCase):
    setUp = admission.OptimizedPackageTests.setUp
    write = admission.OptimizedPackageTests.write
    load = admission.OptimizedPackageTests.load
    v9 = admission.OptimizedPackageTests.v9
    v10 = admission.OptimizedPackageTests.v10

    def receiver(self):
        self.v10()
        self.manifest.update(source_revision=RECEIVER_REV, parent_manifest_sha256=COMMON_PARENT)
        self.manifest[RECEIVER] = True

    def combined(self):
        self.receiver()
        self.manifest.update(source_revision=COMBINED_REV, parent_manifest_sha256=RECEIVER_PARENT,
            strict_nearest_gates_enabled=True, strict_nearest_gates_enabled_default=False,
            strict_nearest_gates_runtime_qualified=False, strict_nearest_source_manifest_sha256=NN_SOURCE)

    def test_receiver_revision_only_activates_its_five_verified_features(self):
        self.receiver(); flags = {}
        self.assertIs(self.load(flags), True)
        self.assertEqual(flags, dict(dict.fromkeys(admission.FEATURES+(admission.FACTORY_FEATURE, RECEIVER), True),
                                     **{NN: False}))

    def test_combined_revision_activates_six_only_after_full_verification(self):
        self.combined(); flags = {}
        self.assertIs(self.load(flags), True)
        self.assertEqual(flags, dict.fromkeys(admission.ALL_FEATURES, True))
        self.assertNotIn('physical_factory', sys.modules)
        self.assertNotIn('rclpy', sys.modules)

    def test_receiver_feature_exact_true_and_parent_pair_before_publication(self):
        self.receiver(); original = copy.deepcopy(self.manifest)
        for key, value in [(RECEIVER, x) for x in (False, 0, 1, None, 'true')] + [
                ('parent_manifest_sha256', RECEIVER_PARENT), ('source_revision', 'unreviewed_receiver')]:
            self.manifest = copy.deepcopy(original); self.manifest[key] = value; flags = {}; before = sys.path.copy()
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.load(flags)
            self.assertEqual(flags, {}); self.assertEqual(sys.path, before)
        self.manifest = copy.deepcopy(original); del self.manifest[RECEIVER]
        with self.assertRaises(ValueError): self.load({})

    def test_combined_nearest_contract_and_exact_ancestry_are_required(self):
        self.combined(); original = copy.deepcopy(self.manifest)
        cases = [(NN, x) for x in (False, 0, 1, None, 'true')] + [
            ('strict_nearest_gates_enabled_default', x) for x in (True, 0, 1, None)] + [
            ('strict_nearest_gates_runtime_qualified', x) for x in (True, 0, 1, None)] + [
            ('strict_nearest_source_manifest_sha256', '0'*64),
            ('parent_manifest_sha256', COMMON_PARENT), ('parent_manifest_sha256', NN_SOURCE),
            ('source_revision', True), (RECEIVER, False), ('preserved_timing_hooks', 1)]
        for key, value in cases:
            self.manifest = copy.deepcopy(original); self.manifest[key] = value; flags = {}; before = sys.path.copy()
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.load(flags)
            self.assertEqual(flags, {}); self.assertEqual(sys.path, before)

    def test_older_revision_cannot_enable_new_features_from_manifest_or_environment(self):
        self.v10()
        self.manifest.update({RECEIVER: True, NN: True})
        flags = {}
        with patch.dict('os.environ', {'NATIVE_RECEIVER_RECOVERY': 'true', 'NATIVE_STRICT_NEAREST': 'true'}):
            self.assertIs(self.load(flags), True)
        self.assertIs(flags[RECEIVER], False); self.assertIs(flags[NN], False)
        self.receiver(); self.manifest[NN] = True; flags = {}
        self.assertIs(self.load(flags), True)
        self.assertIs(flags[RECEIVER], True); self.assertIs(flags[NN], False)

    def test_late_corruption_and_import_collision_never_publish_combined_features(self):
        self.combined(); digest = self.write(); flags = {}; before = sys.path.copy()
        target = self.root/'physical_ingress.py'; saved = target.read_bytes(); target.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            admission.runner.load_physical_package(self.root, digest, reviewed_features=flags)
        self.assertEqual(flags, {}); self.assertEqual(sys.path, before)
        target.write_bytes(saved)
        with patch.dict(sys.modules, {'inputs': object()}), self.assertRaisesRegex(ValueError, 'isolated_physical'):
            admission.runner.load_physical_package(self.root, digest, reviewed_features=flags)
        self.assertEqual(flags, {}); self.assertEqual(sys.path, before)


class CombinedWiringTests(unittest.TestCase):
    adapter = wiring.TrustedWiringTests.adapter

    def test_six_true_flags_split_into_three_ingress_and_three_factory_options(self):
        calls = self.adapter(dict.fromkeys(wiring.ALL_FEATURES, True))
        self.assertEqual({k:calls[0][1][k] for k in wiring.FEATURES}, dict.fromkeys(wiring.FEATURES, True))
        for key in (wiring.FACTORY_FEATURE, RECEIVER, NN): self.assertNotIn(key, calls[0][1])
        self.assertEqual(calls[2], ('factory', dict(exact_surface_cache_enabled=True,
            postconsumed_causal_raw_expiry_reanchor_enabled=True,
            receiver_commit_causal_raw_expiry_reanchor_enabled=True, strict_nearest_gates_enabled=True)))

    def test_all_false_and_absent_options_do_not_break_legacy_constructor(self):
        for features in (None, {}, dict.fromkeys(wiring.ALL_FEATURES, False)):
            calls = self.adapter(features, legacy=True, cache=False)
            self.assertEqual(calls[0], ('legacy_receiver',)); self.assertEqual(calls[2], ('factory', {}))

    def test_receiver_boolean_alias_and_external_override_are_rejected(self):
        for features in ({RECEIVER: 1}, {RECEIVER: 'true'}, {RECEIVER: None}, {'receiver_RPC_override': True}):
            with self.subTest(features=features), self.assertRaisesRegex(ValueError, 'verified_physical_receiver_features'):
                self.adapter(features)

    def test_no_cli_RPC_or_environment_recovery_switch(self):
        for module in (wiring.runner, wiring.service):
            tree = ast.parse(Path(module.__file__).read_bytes())
            arguments = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and
                         isinstance(n.func, ast.Attribute) and n.func.attr == 'add_argument']
            self.assertFalse(any(RECEIVER in str(getattr(n.args[0], 'value', '')) for n in arguments))
        tree = ast.parse(Path(wiring.service.__file__).read_bytes())
        self.assertFalse(any(isinstance(n, ast.Constant) and n.value == RECEIVER for n in ast.walk(tree)))


if __name__ == '__main__': unittest.main()
