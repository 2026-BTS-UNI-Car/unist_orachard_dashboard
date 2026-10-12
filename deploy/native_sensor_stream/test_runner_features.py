"""Trusted constructor wiring only: no ROS, sockets or subprocess owner."""
import ast
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import stream_runner as runner
import native_observation_service as service

HERE = Path(__file__).resolve().parent
FEATURES = ('imu_gap_reanchor_enabled', 'startup_expiry_discard_enabled',
            'current_expiry_reanchor_enabled')
FACTORY_FEATURE = 'postconsumed_causal_raw_expiry_reanchor_enabled'
NEAREST_FEATURE = "strict_nearest_gates_enabled"
RECEIVER_FEATURE = "receiver_commit_causal_raw_expiry_reanchor_enabled"
FINAL_FEATURE = "final_scalar_causal_raw_expiry_reanchor_enabled"
ALL_FEATURES = FEATURES + (FACTORY_FEATURE, NEAREST_FEATURE, RECEIVER_FEATURE, FINAL_FEATURE)


class TrustedWiringTests(unittest.TestCase):
    def adapter(self, features=None, legacy=False, cache=True):
        calls = []
        modules = {}
        for name in ('rclpy', 'rclpy.clock', 'common', 'common.native_ownership',
                     'physical_ingress', 'physical_factory', 'stream_rows'):
            modules[name] = ModuleType(name)
        clock_type = SimpleNamespace(SYSTEM_TIME=object())
        class Clock:
            def __init__(self, *, clock_type):self.clock_type=clock_type
            def now(self):return SimpleNamespace(nanoseconds=123)
        class Receiver:
            def __init__(self, **kwargs):calls.append(('receiver', kwargs))
            def reset(self):calls.append(('reset',))
        if legacy:
            class Receiver:
                def __init__(self, *, raw_clock, source_now_ns, source_clock_kind,
                             scan_frame, imu_frame, profile_revision,
                             scan_publisher_gid, imu_publisher_gid, observer_contract_sha256):
                    calls.append(('legacy_receiver',))
                def reset(self):calls.append(('reset',))
        class Factory:
            def __init__(self, receiver, contract, **kwargs):calls.append(('factory', kwargs))
        if legacy:
            class Factory:
                def __init__(self, receiver, contract, *, exact_surface_cache_enabled=False):
                    calls.append(('factory', {'exact_surface_cache_enabled':True} if exact_surface_cache_enabled else {}))
        class Rows:
            def __init__(self, receiver, factory, **kwargs):calls.append(('rows', kwargs))
        modules['rclpy.clock'].Clock=Clock
        modules['rclpy.clock'].ClockType=clock_type
        modules['common.native_ownership'].LinuxRawClock=lambda:object()
        modules['physical_ingress'].PhysicalOriginalIngress=Receiver
        modules['physical_factory'].PhysicalShadowContract=lambda *args:args
        modules['physical_factory'].revision=lambda contract:'verified-profile'
        modules['physical_factory'].PhysicalNativeShadowFactory=Factory
        modules['stream_rows'].StreamObserverRows=Rows
        args=SimpleNamespace(scan_gid='11'*16,imu_gid='22'*16,
                             observer_contract_sha256='a'*64,physical_surface_cache_enabled=cache)
        if features is not None:args.physical_reviewed_features=features
        with patch.dict(sys.modules, modules):
            result=runner.make_adapter(args)
        self.assertIsInstance(result, Rows)
        return calls

    def test_three_verified_true_flags_reach_receiver_unchanged(self):
        calls=self.adapter(dict.fromkeys(FEATURES, True))
        options=calls[0][1]
        self.assertEqual({key:options[key] for key in FEATURES}, dict.fromkeys(FEATURES, True))
        self.assertEqual(options['scan_publisher_gid'], bytes.fromhex('11'*16))
        self.assertEqual(options['imu_publisher_gid'], bytes.fromhex('22'*16))
        self.assertEqual(calls[1], ('reset',))
        self.assertEqual(calls[2], ('factory', {'exact_surface_cache_enabled':True}))

    def test_old_receiver_gets_no_new_keywords_even_when_verified_dict_is_false(self):
        for features in (None, {}, dict.fromkeys(FEATURES, False)):
            calls=self.adapter(features, legacy=True, cache=False)
            self.assertEqual(calls[0], ('legacy_receiver',))
            self.assertEqual(calls[2], ('factory', {}))

    def test_false_options_are_omitted_and_true_option_is_not_aliased(self):
        features=dict.fromkeys(FEATURES,False)
        features['imu_gap_reanchor_enabled']=True
        calls=self.adapter(features)
        kwargs=calls[0][1]
        self.assertIs(kwargs['imu_gap_reanchor_enabled'],True)
        self.assertNotIn('startup_expiry_discard_enabled',kwargs)
        self.assertNotIn('current_expiry_reanchor_enabled',kwargs)
        self.assertEqual(features, {'imu_gap_reanchor_enabled':True,
                                   'startup_expiry_discard_enabled':False,
                                   'current_expiry_reanchor_enabled':False})

    def test_untrusted_option_shape_and_boolean_alias_reject(self):
        class Derived(dict):pass
        for features in ([],Derived(),{'environment_override':True},
                         {'imu_gap_reanchor_enabled':1}, {'startup_expiry_discard_enabled':'true'}):
            with self.subTest(features=features),self.assertRaisesRegex(ValueError,'verified_physical_receiver_features'):
                self.adapter(features)

    def test_main_and_service_assign_fresh_dict_and_pass_same_dict_to_loader(self):
        for module,variable in ((runner,'args'),(service,'bound')):
            tree=ast.parse(Path(module.__file__).read_bytes())
            main=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='main')
            selected=[]
            for node in main.body:
                if isinstance(node,ast.Assign) and any(isinstance(t,ast.Attribute) and
                    t.attr in ('physical_reviewed_features','physical_surface_cache_enabled') for t in node.targets):
                    selected.append(node)
            self.assertEqual(len(selected),2)
            self.assertEqual(ast.dump(selected[0].value),ast.dump(ast.Dict(keys=[],values=[])))
            sentinel=object()
            def load(path,sha,*,reviewed_features):
                self.assertEqual(reviewed_features,{})
                reviewed_features.update(dict.fromkeys(ALL_FEATURES,True))
                return sentinel
            bound=SimpleNamespace(package='verified-kit',package_manifest_sha256='a'*64)
            namespace={variable:bound,'load_physical_package':load,
                       'stream_runner':SimpleNamespace(load_physical_package=load)}
            isolated=ast.Module(body=selected,type_ignores=[])
            exec(compile(ast.fix_missing_locations(isolated),'<exact-entrypoint-wiring>','exec'),namespace)
            self.assertEqual(bound.physical_reviewed_features,dict.fromkeys(ALL_FEATURES,True))
            self.assertIs(bound.physical_surface_cache_enabled,sentinel)

    def test_factory_feature_does_not_leak_to_receiver_constructor(self):
        calls=self.adapter(dict.fromkeys(ALL_FEATURES,True))
        self.assertNotIn(FACTORY_FEATURE,calls[0][1])
        self.assertNotIn(RECEIVER_FEATURE,calls[0][1])
        self.assertNotIn(NEAREST_FEATURE,calls[0][1])
        self.assertEqual({key:calls[0][1][key] for key in FEATURES},dict.fromkeys(FEATURES,True))
        self.assertEqual(calls[2],('factory',{'exact_surface_cache_enabled':True,FACTORY_FEATURE:True,NEAREST_FEATURE:True,RECEIVER_FEATURE:True,FINAL_FEATURE:True}))

    def test_false_factory_feature_omitted_for_legacy_factory_signature(self):
        for features in ({FACTORY_FEATURE:False},dict.fromkeys(ALL_FEATURES,False)):
            calls=self.adapter(features,legacy=True,cache=False)
            self.assertEqual(calls[2],('factory',{}))

    def test_factory_feature_exact_bool_not_integer_or_external_feature_name(self):
        for features in ({FACTORY_FEATURE:1},{FACTORY_FEATURE:'true'},{'factory_override':True}):
            with self.subTest(features=features),self.assertRaisesRegex(ValueError,'verified_physical_receiver_features'):
                self.adapter(features)

    def test_service_input_is_adapter_proposal_not_retained_diagnostic_history(self):
        tree=ast.parse(Path(service.__file__).read_bytes())
        assignments=[node for node in ast.walk(tree) if isinstance(node,ast.Assign) and
                     any(isinstance(t,ast.Subscript) and isinstance(t.slice,ast.Constant) and
                         t.slice.value=='input' for t in node.targets)]
        self.assertEqual(len(assignments),1)
        node=assignments[0]
        self.assertIn('proposal_input',ast.dump(node.value))
        self.assertNotIn('latest',ast.dump(node.value))
        # The input expression is gated by the exact `available` assignment.
        gates=[n for n in ast.walk(tree) if isinstance(n,ast.Assign) and
               any(isinstance(t,ast.Name) and t.id=='available' for t in n.targets)]
        self.assertEqual(len(gates),1)
        exact=compile(ast.fix_missing_locations(ast.Module(body=[gates[0],node],type_ignores=[])),
                      '<exact-input-expression>','exec')
        # Exact service expression retains None during WAIT despite old history.
        owner=SimpleNamespace(owned=True,cancelled=False,first_failure_reason=None,latest={'old':True})
        adapter=SimpleNamespace(proposal_input=lambda:None)
        namespace={'owner':owner,'adapter':adapter,'result':{}}
        exec(exact,namespace)
        self.assertIsNone(namespace['result']['input'])
        adapter.proposal_input=lambda:{'fresh_pair':True}
        exec(exact,namespace)
        self.assertEqual(namespace['result']['input'],{'fresh_pair':True})
        owner.cancelled=True
        exec(exact,namespace)
        self.assertIsNone(namespace['result']['input'])

    def test_default_main_and_service_remain_inert_without_package_IO(self):
        with patch.object(runner,'load_physical_package',side_effect=AssertionError('unexpected package IO')):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(runner.main([]),0)
            self.assertIn('"process_started": false',out.getvalue())
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(service.main([]),0)
            self.assertIn('"source_started": false',out.getvalue())


if __name__=='__main__':unittest.main()
