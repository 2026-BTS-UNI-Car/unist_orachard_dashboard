"""V16 exact ancestry; eighth flag remains factory-only, no ninth RPC switch."""
import copy,unittest
import test_preparation_features as preparation
REV='native_v16_exact_cache_commit_initial_causal_raw_expiry_wait'
PARENT='4669f543c358ca563c854dbddd70ca889d64f1837e012f29cc4dbc4f5a87818e'
class CommitInitialLoaderTests(preparation.PreparationLoaderTests):
    def initial(self):
        self.preparation();self.manifest.update(source_revision=REV,parent_manifest_sha256=PARENT,commit_initial_causal_raw_expiry_wait_enabled=True)
    def test_v16_exact_eight_features(self):
        self.initial();flags={};self.assertIs(self.load(flags),True)
        self.assertEqual(len(flags),8);self.assertIs(flags[preparation.KEY],True)
        self.assertIs(flags['strict_nearest_gates_enabled'],False)
    def test_v16_policy_literal_true_ancestry_and_no_partial_features(self):
        self.initial();baseline=copy.deepcopy(self.manifest)
        for key,value in (('commit_initial_causal_raw_expiry_wait_enabled',False),('commit_initial_causal_raw_expiry_wait_enabled',0),('commit_initial_causal_raw_expiry_wait_enabled',1),('commit_initial_causal_raw_expiry_wait_enabled',None),('commit_initial_causal_raw_expiry_wait_enabled','true'),('parent_manifest_sha256','0'*64),(preparation.KEY,False)):
            self.manifest=copy.deepcopy(baseline);self.manifest[key]=value;flags={}
            with self.assertRaises(ValueError):self.load(flags)
            self.assertEqual(flags,{})
        self.manifest=copy.deepcopy(baseline);del self.manifest['commit_initial_causal_raw_expiry_wait_enabled']
        with self.assertRaises(ValueError):self.load({})
if __name__=='__main__':unittest.main()
