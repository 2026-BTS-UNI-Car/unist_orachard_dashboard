"""Reviewed V13 final-leaf routing only; no ROS, process or sensor owner."""
import ast
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import test_optimized_package as admission
import test_runner_features as wiring
import test_receiver_nearest_integration as receiver_tests

FINAL=admission.FINAL_FEATURE
NN=admission.NEAREST_FEATURE
REV='native_v13_exact_cache_final_scalar_causal_raw_expiry_wait'
PARENT='68bd1239327ca8701004fd284484223b1ddfabd9105ace933ee8ead9ec1ed5a7'

class FinalAdmissionTests(unittest.TestCase):
    setUp=admission.OptimizedPackageTests.setUp
    write=admission.OptimizedPackageTests.write
    load=admission.OptimizedPackageTests.load
    v9=admission.OptimizedPackageTests.v9
    v10=admission.OptimizedPackageTests.v10
    receiver=receiver_tests.CombinedAdmissionTests.receiver
    def final(self):
        self.receiver();self.manifest.update(source_revision=REV,parent_manifest_sha256=PARENT)
        self.manifest[FINAL]=True
    def test_verified_v13_six_recovery_features_and_nearest_false(self):
        self.final();features={};self.assertIs(self.load(features),True)
        self.assertEqual(features,dict(dict.fromkeys(admission.ALL_FEATURES,True),**{NN:False}))
        self.assertNotIn('rclpy',sys.modules)
    def test_new_flag_exact_true_and_missing_rejected_without_publication(self):
        self.final();old=copy.deepcopy(self.manifest)
        for value in (False,0,1,None,'true'):
            self.manifest=copy.deepcopy(old);self.manifest[FINAL]=value;features={}
            with self.subTest(value=value),self.assertRaises(ValueError):self.load(features)
            self.assertEqual(features,{})
        self.manifest=copy.deepcopy(old);del self.manifest[FINAL]
        with self.assertRaises(ValueError):self.load({})
    def test_exact_revision_parent_and_nearest_disabled(self):
        self.final();old=copy.deepcopy(self.manifest)
        for key,value in (('parent_manifest_sha256','0'*64),('source_revision','unreviewed_final'),
                          ('source_revision',True),(NN,True),(NN,0),(NN,1),(NN,None)):
            self.manifest=copy.deepcopy(old);self.manifest[key]=value;features={}
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.load(features)
            self.assertEqual(features,{})
    def test_older_reviewed_revision_cannot_enable_final_from_extra_metadata(self):
        for make in (self.v10,self.receiver):
            make();self.manifest[FINAL]=True;features={}
            with patch.dict('os.environ',{'NATIVE_FINAL_SCALAR_RECOVERY':'true'}):self.assertIs(self.load(features),True)
            self.assertIs(features[FINAL],False)
    def test_late_source_corruption_or_import_collision_never_publishes(self):
        self.final();digest=self.write();features={};before=sys.path.copy()
        path=self.root/'physical_factory.py';saved=path.read_bytes();path.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'source_changed'):
            admission.runner.load_physical_package(self.root,digest,reviewed_features=features)
        self.assertEqual(features,{});self.assertEqual(sys.path,before);path.write_bytes(saved)
        with patch.dict(sys.modules,{'inputs':object()}),self.assertRaisesRegex(ValueError,'isolated_physical'):
            admission.runner.load_physical_package(self.root,digest,reviewed_features=features)
        self.assertEqual(features,{})

class FinalWiringTests(unittest.TestCase):
    adapter=wiring.TrustedWiringTests.adapter
    def test_factory_only_new_true_option_and_legacy_false_omitted(self):
        calls=self.adapter({FINAL:True})
        self.assertNotIn(FINAL,calls[0][1]);self.assertIs(calls[2][1][FINAL],True)
        for features in (None,{},dict.fromkeys(wiring.ALL_FEATURES,False)):
            self.assertEqual(self.adapter(features,legacy=True,cache=False)[2],('factory',{}))
    def test_alias_unknown_or_external_override_rejected(self):
        for features in ({FINAL:1},{FINAL:'true'},{FINAL:None},{'final_RPC_override':True}):
            with self.assertRaisesRegex(ValueError,'verified_physical_receiver_features'):self.adapter(features)
    def test_no_new_cli_environment_or_RPC_activation(self):
        for module in (wiring.runner,wiring.service):
            tree=ast.parse(Path(module.__file__).read_bytes())
            args=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='add_argument']
            self.assertFalse(any(FINAL in str(getattr(n.args[0],'value','')) for n in args))
        self.assertNotIn(FINAL,Path(wiring.service.__file__).read_text())

if __name__=='__main__':unittest.main()
