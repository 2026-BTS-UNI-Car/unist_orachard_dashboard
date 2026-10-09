"""V15 exact reviewed preparation flag; legacy routing stays unchanged."""
import copy,unittest
import test_receiver_end_features as end
import test_runner_features as wiring
import test_optimized_package as admission
REV='native_v15_exact_cache_preparation_causal_raw_expiry_wait'
PARENT='b6b8e500cf49481177610f877b837be66ab1c71b5d273fe5326fc887c2b31f48'
KEY='preparation_causal_raw_expiry_reanchor_enabled'
class PreparationLoaderTests(end.ReceiverEndLoaderTests):
    def preparation(self):
        self.end();self.manifest.update(source_revision=REV,parent_manifest_sha256=PARENT)
        self.manifest[KEY]=True
    def test_v15_exact_parent_eighth_flag_and_false_nearest(self):
        self.preparation();flags={};self.assertIs(self.load(flags),True)
        expected=dict(dict.fromkeys(admission.ALL_FEATURES,True),**{admission.NEAREST_FEATURE:False,KEY:True})
        self.assertEqual(flags,expected);self.assertEqual(len(flags),8)
    def test_v15_exact_true_and_ancestry_before_flag_publication(self):
        self.preparation();old=copy.deepcopy(self.manifest)
        for key,value in ((KEY,False),(KEY,0),(KEY,1),(KEY,None),(KEY,'true'),
            ('parent_manifest_sha256','0'*64),('source_revision','unknown_prepare'),
            ('receiver_end_scalar_causal_raw_expiry_wait_enabled',False)):
            self.manifest=copy.deepcopy(old);self.manifest[key]=value;flags={}
            with self.assertRaises(ValueError):self.load(flags)
            self.assertEqual(flags,{})
        self.manifest=copy.deepcopy(old);del self.manifest[KEY]
        with self.assertRaises(ValueError):self.load({})
    def test_old_exact_revision_cannot_enable_new_flag_from_extra_metadata(self):
        for make in (self.v9,self.v10,self.receiver,self.final,self.end):
            make();self.manifest[KEY]=True;flags={};self.assertIs(self.load(flags),True)
            self.assertNotIn(KEY,flags)
class PreparationWiringTests(unittest.TestCase):
    adapter=wiring.TrustedWiringTests.adapter
    def test_factory_only_true_and_old_false_omitted(self):
        calls=self.adapter({KEY:True});self.assertNotIn(KEY,calls[0][1]);self.assertIs(calls[2][1][KEY],True)
        for features in (None,{}, {KEY:False}):
            self.assertEqual(self.adapter(features,legacy=True,cache=False)[2],('factory',{}))
    def test_alias_and_unknown_override_rejected(self):
        for features in ({KEY:1},{KEY:'true'},{KEY:None},{'prepare_RPC_override':True}):
            with self.assertRaises(ValueError):self.adapter(features)
if __name__=='__main__':unittest.main()
