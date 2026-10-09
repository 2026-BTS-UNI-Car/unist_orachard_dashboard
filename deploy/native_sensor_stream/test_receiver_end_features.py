"""V14 reviewed ancestry and existing seven constructor flags only."""
import copy
import unittest
import test_final_scalar_features as final
import test_optimized_package as admission

REV='native_v14_exact_cache_receiver_end_scalar_causal_raw_expiry_wait'
PARENT='4de382364c247de7b3ad05e819b2272e1de50a57d0d9e1c0ffe7d2e12ac85cf4'
KEY='receiver_end_scalar_causal_raw_expiry_wait_enabled'
class ReceiverEndLoaderTests(unittest.TestCase):
    setUp=admission.OptimizedPackageTests.setUp
    write=admission.OptimizedPackageTests.write
    load=admission.OptimizedPackageTests.load
    v9=admission.OptimizedPackageTests.v9
    v10=admission.OptimizedPackageTests.v10
    receiver=final.FinalAdmissionTests.receiver
    final=final.FinalAdmissionTests.final
    def end(self):
        self.final();self.manifest.update(source_revision=REV,parent_manifest_sha256=PARENT)
        self.manifest[KEY]=True
    def test_valid_revision_keeps_seven_flags_no_new_constructor_option(self):
        self.end();flags={};self.assertIs(self.load(flags),True)
        self.assertEqual(flags,dict(dict.fromkeys(admission.ALL_FEATURES,True),**{admission.NEAREST_FEATURE:False}))
        self.assertNotIn(KEY,flags)
    def test_literally_true_policy_required_before_feature_publication(self):
        self.end();old=copy.deepcopy(self.manifest)
        for value in (False,0,1,None,'true'):
            self.manifest=copy.deepcopy(old);self.manifest[KEY]=value;flags={}
            with self.assertRaisesRegex(ValueError,'reviewed_receiver_end_scalar'):self.load(flags)
            self.assertEqual(flags,{})
        self.manifest=copy.deepcopy(old);del self.manifest[KEY]
        with self.assertRaises(ValueError):self.load({})
    def test_exact_parent_known_revision_and_no_nearest_activation(self):
        self.end();old=copy.deepcopy(self.manifest)
        for key,value in (('parent_manifest_sha256',final.PARENT),('source_revision','unreviewed_end'),
                          ('source_revision',True),(admission.NEAREST_FEATURE,True)):
            self.manifest=copy.deepcopy(old);self.manifest[key]=value;flags={}
            with self.assertRaises(ValueError):self.load(flags)
            self.assertEqual(flags,{})
    def test_v13_metadata_cannot_select_v14_source_policy(self):
        self.final();self.manifest[KEY]=True;flags={};self.assertIs(self.load(flags),True)
        self.assertNotIn(KEY,flags);self.assertEqual(len(flags),7)
if __name__=='__main__':unittest.main()
