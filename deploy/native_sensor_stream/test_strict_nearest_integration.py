"""Reviewed source feature routing only; no ROS/subprocess/socket owner."""
import ast
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_optimized_package as admission
import test_runner_features as wiring

NN = 'strict_nearest_gates_enabled'
REV = 'native_private_strict_nearest_gates_default_OFF'
PARENT = 'd24a38bafde135b70db87c50e39303f4d6d82c2267ffeef3f769a45587ab57f1'


class NearestAdmissionTests(unittest.TestCase):
    setUp = admission.OptimizedPackageTests.setUp
    write = admission.OptimizedPackageTests.write
    load = admission.OptimizedPackageTests.load
    v9 = admission.OptimizedPackageTests.v9
    v10 = admission.OptimizedPackageTests.v10
    v6 = admission.OptimizedPackageTests.v6
    v7 = admission.OptimizedPackageTests.v7

    def nearest(self):
        self.v10()
        self.manifest.update(source_revision=REV, parent_manifest_sha256=PARENT,
            strict_nearest_gates_enabled_default=False, strict_nearest_gates_runtime_qualified=False)

    def test_fifth_factory_feature_only_after_complete_source_and_isolation_checks(self):
        self.nearest(); flags = {}
        self.assertIs(self.load(flags), True)
        self.assertEqual(flags, dict(dict.fromkeys(admission.FEATURES+(admission.FACTORY_FEATURE,NN),True), **{admission.RECEIVER_FEATURE:False,admission.FINAL_FEATURE:False}))
        self.assertNotIn('rclpy', sys.modules)

    def test_literal_default_false_and_no_claim_of_runtime_qualification(self):
        self.nearest(); original = copy.deepcopy(self.manifest)
        for key in ('strict_nearest_gates_enabled_default', 'strict_nearest_gates_runtime_qualified'):
            for value in (True, 0, 1, None, 'false'):
                self.manifest = copy.deepcopy(original); self.manifest[key] = value; flags = {}
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'strict_nearest'):
                    self.load(flags)
                self.assertEqual(flags, {})
            self.manifest = copy.deepcopy(original); del self.manifest[key]
            with self.assertRaisesRegex(ValueError, 'strict_nearest'): self.load({})

    def test_wrong_revision_parent_or_recovery_contract_never_exposes_nn(self):
        self.nearest(); original = copy.deepcopy(self.manifest)
        for key, value in (('source_revision', 'native_unreviewed_nearest'),
                ('parent_manifest_sha256', '42e46534f700cacdce08ea9aee64b3fe9667f6bb35a297b99182507d1afd12a1'),
                ('postconsumed_causal_raw_expiry_reanchor_enabled', False),
                ('selected_callback_RAW_TTL_ns', 200000001), ('preserved_timing_hooks', 1)):
            self.manifest = copy.deepcopy(original); self.manifest[key] = value; flags = {}
            with self.subTest(key=key), self.assertRaises(ValueError): self.load(flags)
            self.assertEqual(flags, {})

    def test_late_kernel_corruption_or_import_collision_retains_empty_trusted_dict(self):
        self.nearest(); digest = self.write(); flags = {}; before = sys.path.copy()
        target = self.root/'kernel/raw_localization.py'; saved = target.read_bytes()
        target.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            admission.runner.load_physical_package(self.root, digest, reviewed_features=flags)
        self.assertEqual(flags, {}); self.assertEqual(sys.path, before)
        target.write_bytes(saved)
        with patch.dict(sys.modules, {'physical_factory': object()}), self.assertRaisesRegex(ValueError, 'isolated_physical'):
            admission.runner.load_physical_package(self.root, digest, reviewed_features=flags)
        self.assertEqual(flags, {}); self.assertEqual(sys.path, before)

    def test_old_reviewed_revisions_cannot_activate_nn_from_manifest_or_external_environment(self):
        initial = copy.deepcopy(self.manifest)
        for revision in ('v5', 'v6', 'v7', 'v9', 'v10'):
            self.manifest = copy.deepcopy(initial)
            if revision != 'v5': getattr(self, revision)()
            self.manifest[NN] = True
            self.manifest['strict_nearest_gates_enabled_default'] = False
            flags = {}
            with patch.dict('os.environ', {'NATIVE_STRICT_NEAREST': 'true'}):
                self.assertIs(self.load(flags), True)
            self.assertIs(flags[NN], False)


class NearestWiringTests(unittest.TestCase):
    adapter = wiring.TrustedWiringTests.adapter

    def test_nn_only_reaches_factory_not_original_receiver(self):
        flags = dict.fromkeys(wiring.ALL_FEATURES, True)
        calls = self.adapter(flags)
        self.assertNotIn(NN, calls[0][1])
        self.assertEqual(calls[2], ('factory', dict(exact_surface_cache_enabled=True,
            postconsumed_causal_raw_expiry_reanchor_enabled=True, strict_nearest_gates_enabled=True,
            receiver_commit_causal_raw_expiry_reanchor_enabled=True,
            final_scalar_causal_raw_expiry_reanchor_enabled=True)))

    def test_false_and_absent_nn_send_no_keyword_to_real_legacy_constructor_signature(self):
        for flags in (None, {}, {NN: False}, dict.fromkeys(wiring.ALL_FEATURES, False)):
            calls = self.adapter(flags, legacy=True, cache=False)
            self.assertEqual(calls[0], ('legacy_receiver',))
            self.assertEqual(calls[2], ('factory', {}))

    def test_nn_boolean_alias_or_unknown_feature_is_not_a_toggle(self):
        for flags in ({NN: 1}, {NN: 'true'}, {NN: None}, {'nearest_rpc_override': True}):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, 'verified_physical_receiver_features'):
                self.adapter(flags)

    def test_no_cli_or_RPC_or_environment_control_for_nn(self):
        for module in (wiring.runner, wiring.service):
            tree = ast.parse(Path(module.__file__).read_bytes())
            commands = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and
                        isinstance(n.func, ast.Attribute) and n.func.attr == 'add_argument']
            self.assertFalse(any(NN in str(getattr(n.args[0], 'value', '')) for n in commands))
        methods = [n for n in ast.walk(ast.parse(Path(wiring.service.__file__).read_bytes()))
                   if isinstance(n, ast.Constant) and type(n.value) is str and n.value == NN]
        self.assertEqual(methods, [])


if __name__ == '__main__': unittest.main()
