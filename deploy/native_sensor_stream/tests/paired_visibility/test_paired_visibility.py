"""Consumed DEVELOPMENT fixtures: no physical or holdout qualification."""
import hashlib,inspect,math,struct,sys,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
HERE=Path(__file__).resolve().parent
NATIVE=HERE.parents[1]
sys.path[:0]=[str(NATIVE),str(NATIVE/'optimized-package')]
import paired_keyframe_registration as candidate
from paired_keyframe_registration import register_original_pair
from pair_visibility import paired_visibility
from keyframe_registration import scan_geometry
from committed_native_map import CommittedNativeMapAdapter
from native_map_transport import NativeMapSession
from test_native_map_scan_to_map import scan_cdr,periodic,orchard
from test_native_map_bridge_gates import curved_rows,clutter

K=(HERE/'inputs'/'K.cdr').read_bytes()
H=(HERE/'inputs'/'H.cdr').read_bytes()
KS=hashlib.sha256(K).hexdigest();HS=hashlib.sha256(H).hexdigest()

def run(k=K,h=H,**kw):
 return register_original_pair(k,h,enabled=True,target_sha256=hashlib.sha256(k).hexdigest(),
     current_sha256=hashlib.sha256(h).hexdigest(),max_wall_s=2.,**kw)

def fakefit(x=0.,y=0.,yaw=0.):
 return dict(xy=np.array((x,y)),yaw=yaw,coverage=.5,rmse_m=.04,
     information_eigenvalues=np.array((.2,.4,.5)))

def metrics(vis=.9):
 return dict(F_vis=vis,B_visK=vis,obs=.8,backward_observed_share=.8,rms_f=.04,rms_bK=.04,
     unchanged_full_return_overlap=(.1,.1),full_return_70_percent_pass=False)

class OriginalFixture(unittest.TestCase):
 def test_firstgap_original_identity_search_positive_no_oracle(self):
  r=run()
  self.assertTrue(r['accepted'],r)
  self.assertLess(abs(r['xy'][0]+.163),.02)
  self.assertLess(abs(r['yaw']-.05),.02)
  self.assertFalse(r['runtime_qualified'])
  self.assertFalse(r['physical_accuracy_verified'])
  self.assertFalse(r['runtime_authority'])
  self.assertEqual(r['target_original_CDR_sha256'],KS)
  self.assertEqual(r['current_original_CDR_sha256'],HS)
  self.assertFalse(r['pair_visibility']['full_return_70_percent_pass'])
  self.assertTrue(r['supplemental_full_return_gate_replaced_by_paired_visibility'])
  self.assertEqual(r['source_stamps_ns'],(1791012337849687020,1791012338248586988))
  self.assertEqual((hashlib.sha256(K).hexdigest(),hashlib.sha256(H).hexdigest()),(KS,HS))
 def test_contradictory_nearband_solution_rejected_on_full_original(self):
  kw,kg=scan_geometry(K,KS);hw,hg=scan_geometry(H,HS)
  m=paired_visibility(kw,kg,hw,hg,(.05057757,-.04789025,-.02444114))
  self.assertLess(min(m['F_vis'],m['B_visK']),.7)
  with patch.object(candidate,'fit_surfaces',return_value=fakefit(.05057757,-.04789025,-.02444114)):
   r=run()
  self.assertFalse(r['accepted'])
  self.assertEqual(r['reason'],'unmatched_original_pair_visibility')
 def test_invalid_cdr_sha_and_changed_raw_reject(self):
  with self.assertRaisesRegex(ValueError,'CDR_SHA_changed'):
   register_original_pair(K,H,enabled=True,target_sha256='0'*64,current_sha256=HS)
  bad=H[:-1]+bytes((H[-1]^1,))
  with self.assertRaisesRegex(ValueError,'CDR_SHA_changed'):
   register_original_pair(K,bad,enabled=True,target_sha256=KS,current_sha256=HS)
 def test_reverse_stamp_rejected(self):
  with self.assertRaisesRegex(ValueError,'forward_pair'):
   run(H,K)

class CompetingPolicies(unittest.TestCase):
 def setup_context(self,seeds,screen,solver,vis):
  patches=[patch.object(candidate,'seed_grid',return_value=seeds),
      patch.object(candidate,'_screen_peaks',return_value=screen),
      patch.object(candidate,'fit_surfaces',side_effect=solver),
      patch.object(candidate,'paired_visibility',side_effect=vis),
      patch.object(candidate,'_weak_normal',return_value=.5)]
  for p in patches:p.start();self.addCleanup(p.stop)
 def test_lenient_grid_competitor_uses_visibility_not_old_raw_gate(self):
  seen=[]
  def vis(*a):seen.append(a[-1]);return metrics(.9 if a[-1][0]==0 else .75)
  self.setup_context([(0.,(0.,0.)),(0.,(.2,0.))],[],lambda t,c,a,xy,*z:fakefit(*xy),vis)
  r=run()
  self.assertFalse(r['accepted']);self.assertEqual(r['reason'],'ambiguous_original_pair_visibility')
  self.assertEqual(len(seen),2)
 def test_far_strong_grid_alternative_rejects_even_lower_coverage(self):
  def fit(t,c,a,xy,*z):
   f=fakefit(*xy)
   if xy[0]:f['coverage']=.41;f['rmse_m']=.075
   return f
  self.setup_context([(0.,(0.,0.)),(0.,(.4,0.))],[],fit,lambda *a:metrics())
  r=run();self.assertFalse(r['accepted']);self.assertEqual(r['reason'],'ambiguous_original_pair_visibility')
 def test_screened_competitor_uses_same_visibility_with_lenient_level(self):
  seen=[]
  def vis(*a):seen.append(a[-1]);return metrics(.9 if a[-1][0]==0 else .75)
  self.setup_context([(0.,(0.,0.))],[(.5,0.,0.)],lambda t,c,a,xy,*z:fakefit(*xy),vis)
  r=run();self.assertFalse(r['accepted'])
  self.assertEqual(r['reason'],'ambiguous_screened_original_pair_visibility')
  self.assertEqual(len(seen),2)
 def test_weak_degenerate_screened_solution_remains_competitor_only(self):
  def solver(t,c,a,xy,bm,ba,d):
   if xy[0]:d['pose_information_degenerate']+=1;return None
   return fakefit()
  self.setup_context([(0.,(0.,0.))],[(.5,0.,0.)],solver,lambda *a:metrics(.9 if a[-1][0]==0 else .75))
  with patch.object(candidate,'_fit_free',return_value=fakefit(.5,0.)) as free:
   r=run()
  self.assertFalse(r['accepted']);self.assertEqual(free.call_count,1)
  self.assertEqual(r['reason'],'ambiguous_screened_original_pair_visibility')
 def test_only_degenerate_fit_cannot_be_selected_best(self):
  def fail(t,c,a,xy,bm,ba,d):d['pose_information_degenerate']+=1;return None
  self.setup_context([(0.,(0.,0.))],[(.5,0.,0.)],fail,lambda *a:metrics())
  with patch.object(candidate,'_fit_free',return_value=fakefit()) as free:r=run()
  self.assertFalse(r['accepted']);self.assertEqual(free.call_count,0)

class RuntimeGuards(unittest.TestCase):
 def test_default_off_before_cdr_access_and_typed_flags(self):
  self.assertFalse(register_original_pair(None,None)['accepted'])
  self.assertIs(inspect.signature(register_original_pair).parameters['enabled'].default,False)
  self.assertIs(inspect.signature(CommittedNativeMapAdapter.__init__).parameters['paired_visibility_bridge_enabled'].default,False)
  self.assertIs(inspect.signature(NativeMapSession.__init__).parameters['paired_visibility_bridge_enabled'].default,False)
  with self.assertRaisesRegex(ValueError,'typed_paired_visibility'):
   register_original_pair(K,H,enabled=1)
  with self.assertRaisesRegex(ValueError,'requires_original_C1bG'):
   NativeMapSession(CommittedNativeMapAdapter,map_epoch='e'*32,paired_visibility_bridge_enabled=True)
  with self.assertRaisesRegex(ValueError,'exclusive_bridge_policy'):
   NativeMapSession(CommittedNativeMapAdapter,map_epoch='e'*32,paired_visibility_bridge_enabled=True,
       keyframe_bridge_gates_enabled=True,scan_to_map_bridge_enabled=True)
 def test_Stop_before_search(self):
  with patch.object(candidate,'fit_surfaces') as f:r=run(cancelled=lambda:True)
  self.assertFalse(r['accepted']);self.assertEqual(f.call_count,0)
 def test_fault_or_Stop_during_search_never_returns_accept(self):
  for reason in ('factory_fault','Stop_intent'):
   flags={reason:False}
   def f(*a):flags[reason]=True;return fakefit()
   with patch.object(candidate,'fit_surfaces',side_effect=f):
    r=run(cancelled=lambda:flags[reason])
   self.assertFalse(r['accepted'],reason)
   self.assertIn('cancelled',r['reason'])
 def test_deadline_cancels_before_search(self):
  with patch.object(candidate.time,'monotonic',side_effect=[0.,3.]),patch.object(candidate,'fit_surfaces') as f:
   r=run()
  self.assertFalse(r['accepted']);self.assertEqual(f.call_count,0)
 def test_nonfinite_deadline_rejects(self):
  with self.assertRaisesRegex(ValueError,'deadline'):
   register_original_pair(K,H,enabled=True,target_sha256=KS,current_sha256=HS,max_wall_s=float('nan'))
 def test_original_fit_nonfinite_contract_restored(self):
  for field in ('xy','yaw','coverage','rmse_m','information_eigenvalues'):
   f=fakefit();f[field]=float('nan')
   with patch.object(candidate,'fit_surfaces',return_value=f),self.assertRaisesRegex(ValueError,'nonfinite_original_pair_fit'):
    run()
 def test_Stop_during_alias_work_cancels_before_accept(self):
  state={'stop':False}
  def screen(*args):state['stop']=True;return [(.5,0.,0.)]
  with patch.object(candidate,'seed_grid',return_value=[(0.,(0.,0.))]),patch.object(candidate,'fit_surfaces',return_value=fakefit()),patch.object(candidate,'paired_visibility',return_value=metrics()),patch.object(candidate,'_weak_normal',return_value=.5),patch.object(candidate,'_screen_peaks',side_effect=screen):
   r=run(cancelled=lambda:state['stop'])
  self.assertFalse(r['accepted']);self.assertIn('cancelled',r['reason'])
 def test_invalid_polar_angle_increment_is_rejected(self):
  # CDR metadata starts after encapsulation + Header alignment. Find by decoding fixture's seven float metadata.
  bad=bytearray(H)
  wire,_=scan_geometry(H,HS)
  packed=struct.pack('<7f',*wire.metadata)
  offset=bad.find(packed);self.assertGreater(offset,0)
  bad[offset+8:offset+12]=struct.pack('<f',0.)
  with self.assertRaises(ValueError):run(h=bytes(bad))

class SyntheticAliases(unittest.TestCase):
 def test_irregular_original_rays_positive(self):
  segs=clutter()
  k=scan_cdr(segs,(0.,0.,0.),1_000_000_000)
  h=scan_cdr(segs,(.4,.03,.02),2_000_000_000)
  r=run(k,h)
  self.assertTrue(r['accepted'],r)
  self.assertLess(np.linalg.norm(r['xy']-np.array((.4,.03))),.05)
 def test_periodic_corridor_must_reject_alias(self):
  k=scan_cdr(periodic(),(0.,0.,0.),1_000_000_000,max_range=8.)
  h=scan_cdr(periodic(),(1.05,0.,0.),2_000_000_000,max_range=8.)
  r=run(k,h,seed_center=(1.05,0.,0.))
  self.assertFalse(r['accepted'],r)
 def test_curved_rows_must_not_accept_rotated_period(self):
  segs=curved_rows();period=2.6/30.
  for a in (.4*period,.6*period):
   true=(30*math.sin(a),30-30*math.cos(a),a)
   wrong=(30*math.sin(a-period),30-30*math.cos(a-period),a-period)
   k=scan_cdr(segs,(0.,0.,0.),1_000_000_000,max_range=8.)
   h=scan_cdr(segs,true,2_000_000_000,max_range=8.)
   r=run(k,h,seed_center=tuple(map(float,wrong)))
   if r['accepted']:
    self.assertLess(np.linalg.norm(r['xy']-np.array(true[:2])),.1)
    self.assertLess(abs(r['yaw']-a),math.radians(1))

if __name__=='__main__':unittest.main(verbosity=2)
