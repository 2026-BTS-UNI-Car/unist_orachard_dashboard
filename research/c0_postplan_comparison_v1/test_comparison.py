"""Real source classes, generated scalar data; no sockets, sensors or threads."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import ast,copy,hashlib,importlib.util,json,sys,threading,time,unittest

K=Path(__file__).resolve().parent;U=Path('D:/Develop/UBTS');B=U/'dashboard-live-raw'
sys.path[:0]=[str(B),str(B/'deploy/native_sensor_shadow/physical-package')]
def load(name,relative):
 p=K/'overlays'/relative;s=importlib.util.spec_from_file_location(name,p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
O=load('comparison_observation','dashboard_server/autonomy_observation.py')
R=load('comparison_rows','deploy/native_sensor_stream/stream_rows.py')
M=load('comparison_map_rows','deploy/native_sensor_stream/stream_rows_map.py')
S=load('comparison_service','deploy/native_sensor_stream/native_observation_service.py')
C=load('comparison_client','dashboard_server/native_observation_client.py')
def row(seq=1,age=1_000_000):
 stamp=1_000_000_000+seq*100_000_000;receipt=100_000_000_000+seq*100_000_000
 return dict(context=dict(source_session='generated-only',epoch='generated-epoch',generation=1,
  source_clock_domain='SIM_SOURCE',receipt_clock_domain='SIM_RAW'),
  source_now_ns=stamp+age,receipt_now_ns=receipt+age,
  scan=dict(sequence=seq,frame_id='laser',stamp_ns=stamp,receipt_ns=receipt,ranges=[2.]*24),
  imu=dict(stamp_ns=stamp-1_000_000,receipt_ns=receipt-1_000_000),
  motion=dict(previous_scan_stamp_ns=stamp-100_000_000,current_scan_stamp_ns=stamp,receipt_ns=receipt))
def compact(r):
 return dict(schema='ubts.native_observation.control_comparison.v1',context=dict(r['context']),
  scan={k:r['scan'][k]for k in ('sequence','frame_id','stamp_ns','receipt_ns')},
  imu=dict(r['imu']),motion=dict(r['motion']),source_now_ns=r['source_now_ns'],receipt_now_ns=r['receipt_now_ns'])
def fixture(provider=None,comparison=None,action=None):
 context=SimpleNamespace(cancelled=False,published=[])
 def publish(d,e):context.published.append((copy.deepcopy(d),copy.deepcopy(e)));return True
 context.publish=publish
 parent=SimpleNamespace(lock=threading.RLock(),input_provider=provider or (lambda:None),
  comparison_provider=comparison,clock=None,input_age_ns=250_000_000,cruise_percent=10.,preference_version=0)
 planner=SimpleNamespace(calls=0)
 def plan(value,cruise_percent):
  planner.calls+=1
  if action:action(parent)
  return O.zero_decision('GENERATED_ONLY')
 planner.plan=plan;parent.planner=planner
 w=O._ObservationWorker(parent,context);parent.resource=w
 return parent,w,context,planner
def adapter(r,module=R):
 a=module.StreamObserverRows.__new__(module.StreamObserverRows)
 a._lock=threading.RLock();a._stopping=threading.Event();a.fault=None;a._proposal_input=r
 a._binding=(True,True,True,True,True)
 for name in ('preparation','receiver','final','nearest','registration'):
  setattr(a,'_factory_'+name+'_feature',lambda:True)
 a._fail=lambda error:setattr(a,'fault',str(error))
 a.ingress=SimpleNamespace(source_now=lambda:r['source_now_ns'],raw=SimpleNamespace(sample=lambda:SimpleNamespace(ns=r['receipt_now_ns'])))
 return a
def runner(a):
 owner=SimpleNamespace(session='owner',generation=1,owned=True,cancelled=False,first_failure_reason=None)
 resource=SimpleNamespace(adapter=a,context=SimpleNamespace(session='owner',generation=1))
 value=SimpleNamespace(lock=threading.RLock(),resource=resource,lifecycle=SimpleNamespace(status=lambda:owner))
 return value,owner
def funcs(raw):
 t=ast.parse(raw);out={}
 for n in t.body:
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):out[n.name]=ast.dump(n,include_attributes=False)
  elif isinstance(n,ast.ClassDef):
   for f in n.body:
    if isinstance(f,(ast.FunctionDef,ast.AsyncFunctionDef)):out[n.name+'.'+f.name]=ast.dump(f,include_attributes=False)
 return out

class ComparisonTests(unittest.TestCase):
 def test_original_getters_guards_and_kernel_paths_exact(self):
  pins=json.loads((K/'SOURCE_PINS.json').read_bytes());changed={
   'dashboard_server/autonomy_observation.py':{'_ObservationWorker.current_clock','AutonomyObservation.__init__'},
   'dashboard_server/native_observation_client.py':set(),
   'deploy/native_sensor_stream/stream_rows.py':set(),
   'deploy/native_sensor_stream/stream_rows_map.py':set(),
   'deploy/native_sensor_stream/native_observation_service.py':{'main'},
   'dashboard_server/app.py':{'create_server'}}
  checked=0
  for rel,p in pins.items():
   self.assertEqual(hashlib.sha256((K/'overlays'/rel).read_bytes()).hexdigest(),p['overlay_SHA256'])
   if not rel.endswith('.py'):continue
   old=(U/p['source']).read_bytes();self.assertEqual(hashlib.sha256(old).hexdigest(),p['source_SHA256'])
   before=funcs(old);after=funcs((K/'overlays'/rel).read_bytes())
   for name,v in before.items():
    if name not in changed[rel]:self.assertEqual(v,after[name],rel+':'+name);checked+=1
  self.assertGreater(checked,100)
 def test_default_legacy_two_full_reads_and_constructor_default(self):
  self.assertIsNone(O.AutonomyObservation(lambda x,y:None).comparison_provider)
  with self.assertRaisesRegex(ValueError,'explicit_latest_input'):O.AutonomyObservation(comparison_provider=lambda:None)
  calls=[];p,w,c,q=fixture(lambda:calls.append('full') or row());w.read_input('poll_input');w.process(row())
  self.assertEqual(calls,['full','full']);self.assertEqual(w.decisions,1)
 def test_scalar_fresh_same_and_new_no_second_full_getter(self):
  for n in (1,2):
   def forbidden():raise AssertionError('full getter was called')
   p,w,c,q=fixture(forbidden,lambda:compact(row(n)));w.process(row())
   self.assertEqual(w.decisions,1);self.assertEqual(q.calls,1)
   self.assertEqual(c.published[-1][1]['source']['scan_sequence'],1)
 def test_same_sequence_any_endpoint_mutation_rejected(self):
  for key,field in (('scan','stamp_ns'),('scan','receipt_ns'),('imu','stamp_ns'),('imu','receipt_ns'),('motion','previous_scan_stamp_ns')):
   x=compact(row());x[key][field]+=1;p,w,c,q=fixture(comparison=lambda:x)
   with self.assertRaises(ValueError):w.process(row())
   self.assertEqual(w.decisions,0)
 def test_new_sequence_frontier_recombination_and_causality_rejected(self):
  variants=[]
  for key,field,v in (('motion','previous_scan_stamp_ns',row()['scan']['stamp_ns']-1),
    ('scan','stamp_ns',row()['scan']['stamp_ns']),('scan','receipt_ns',row()['scan']['receipt_ns']),
    ('imu','stamp_ns',row(2)['scan']['stamp_ns']+1),('motion','receipt_ns',row(2)['scan']['receipt_ns']-1)):
   x=compact(row(2));x[key][field]=v;variants.append(x)
  x=compact(row(2));x['imu']['stamp_ns']=row()['imu']['stamp_ns'];variants.append(x)
  for x in variants:
   p,w,c,q=fixture(comparison=lambda:x)
   with self.assertRaises(ValueError):w.process(row())
   self.assertEqual(w.decisions,0)
 def test_none_never_falls_back_or_uses_test_clock(self):
  p,w,c,q=fixture(lambda:(_ for _ in ()).throw(AssertionError('fallback')),lambda:None)
  p.clock=lambda:dict(source_now_ns=row()['source_now_ns'],receipt_now_ns=row()['receipt_now_ns'],source_clock_domain='SIM_SOURCE',receipt_clock_domain='SIM_RAW')
  w.process(row());self.assertEqual(w.decisions,0);self.assertEqual(c.published[-1][0]['reason'],'WAIT_INPUT')
 def test_fresh_latest_cannot_rescue_expired_planned_row(self):
  p,w,c,q=fixture(comparison=lambda:compact(row(4)));w.process(row())
  self.assertEqual(w.decisions,0);self.assertEqual(c.published[-1][0]['reason'],'WAIT_STALE_INPUT')
 def test_planned_210ms_keeps_original_observation250_not_server200(self):
  r=row();latest=row(2);latest['source_now_ns']=r['scan']['stamp_ns']+210_000_000
  latest['receipt_now_ns']=r['scan']['receipt_ns']+210_000_000
  a=adapter(latest);p,w,c,q=fixture(comparison=a.proposal_comparison);w.process(r)
  self.assertEqual(w.decisions,1);self.assertEqual(w.age['scan_receipt_ns'],210_000_000)
 def test_scalar_shape_context_frame_rewind_and_unknown_data_rejected(self):
  variants=[]
  for mutate in (lambda x:x['context'].update(epoch='new'),lambda x:x['scan'].update(frame_id='other'),
   lambda x:x['scan'].update(sequence=0),lambda x:x.update(ranges=[]),lambda x:x.update(source_now_ns=float('nan'))):
   x=compact(row());mutate(x);variants.append(x)
  for x in variants:
   p,w,c,q=fixture(comparison=lambda:x)
   with self.assertRaises(ValueError):w.process(row())
 def test_stop_preference_and_latched_stop_preserved(self):
  p,w,c,q=fixture(comparison=lambda:compact(row()),action=lambda p:setattr(p.resource.context,'cancelled',True));w.process(row());self.assertFalse(c.published)
  p,w,c,q=fixture(comparison=lambda:compact(row()),action=lambda p:setattr(p,'preference_version',1));w.process(row());self.assertFalse(c.published)
  p,w,c,q=fixture(comparison=lambda:None);w.planner_stop_reason='latched_permanent_stop';w.process(row())
  self.assertEqual(c.published[-1][0]['decision'],'STOP');self.assertEqual(c.published[-1][0]['reason'],'latched_permanent_stop')
 def test_adapter_never_deepcopies_or_visits_ranges(self):
  class Forbidden:
   def __deepcopy__(self,m):raise AssertionError('ranges copied')
   def __iter__(self):raise AssertionError('ranges visited')
  for module in (R,M):
   r=row();r['scan']['ranges']=Forbidden();a=adapter(r,module);self.assertEqual(a.proposal_comparison()['scan']['sequence'],1)
   with self.assertRaisesRegex(AssertionError,'ranges copied'):a.proposal_input()
 def test_adapter_exact_latest_source_and_all_three_raw_ttls(self):
  for module in (R,M):
   for key in ('scan','imu','motion'):
    r=row();r[key]['receipt_ns']=r['receipt_now_ns']-module.RECEIPT_AGE_NS;a=adapter(r,module);self.assertIsNone(a.proposal_comparison());self.assertIsNone(a.proposal_input())
   for key in ('scan','imu'):
    r=row();r[key]['stamp_ns']=r['source_now_ns']-module.SOURCE_AGE_NS;a=adapter(r,module);self.assertIsNone(a.proposal_comparison());self.assertIsNone(a.proposal_input())
   for feature in ('preparation','receiver','final','nearest','registration'):
    a=adapter(row(),module);setattr(a,'_factory_'+feature+'_feature',lambda:False);self.assertIsNone(a.proposal_comparison());self.assertIsNotNone(a.fault)
   for mutation in ('stopping','fault','no_row'):
    a=adapter(row(),module)
    if mutation=='stopping':a._stopping.set()
    elif mutation=='fault':a.fault='fault'
    else:a._proposal_input=None
    self.assertIsNone(a.proposal_comparison())
 def test_map_enabled_actual_adapter_and_source_manifest_dispatch(self):
  a=adapter(row(),M);v,owner=runner(a);self.assertEqual(S.read_control_comparison(v),compact(row()))
  source=K/'overlays/deploy/native_sensor_stream';manifest=json.loads((source/'NATIVE_MAP_SOURCE.json').read_bytes())
  self.assertEqual(manifest['files_sha256']['stream_rows_map.py'],hashlib.sha256((source/'stream_rows_map.py').read_bytes()).hexdigest())
  self.assertIn(hashlib.sha256((source/'NATIVE_MAP_SOURCE.json').read_bytes()).hexdigest(),(source/'native_observation_service.py').read_text(encoding='utf-8'))
  r1=funcs((source/'stream_rows.py').read_bytes())['StreamObserverRows.proposal_comparison']
  r2=funcs((source/'stream_rows_map.py').read_bytes())['StreamObserverRows.proposal_comparison'];self.assertEqual(r1,r2)
  pins=json.loads((K/'SOURCE_PINS.json').read_bytes());before=json.loads((U/pins['deploy/native_sensor_stream/NATIVE_MAP_SOURCE.json']['source']).read_bytes())
  before['files_sha256']['stream_rows_map.py']=hashlib.sha256((source/'stream_rows_map.py').read_bytes()).hexdigest()
  self.assertEqual(before,manifest)
  for module in (R,M):self.assertEqual((module.SOURCE_AGE_NS,module.RECEIPT_AGE_NS),(250_000_000,200_000_000))
 def test_service_exact_owner_resource_before_and_after_read(self):
  a=adapter(row());v,owner=runner(a);self.assertIsNotNone(S.read_control_comparison(v))
  original=a.proposal_comparison
  for mutation in ('cancelled','failure','session','generation','resource','adapter'):
   a=adapter(row());v,owner=runner(a);original=a.proposal_comparison
   def changed():
    result=original()
    if mutation=='cancelled':owner.cancelled=True
    elif mutation=='failure':owner.first_failure_reason='Stop'
    elif mutation=='session':owner.session='new'
    elif mutation=='generation':owner.generation+=1
    elif mutation=='resource':v.resource=SimpleNamespace(adapter=a,context=SimpleNamespace(session='owner',generation=1))
    else:v.resource.adapter=object()
    return result
   a.proposal_comparison=changed;self.assertIsNone(S.read_control_comparison(v))
  a=adapter(row());v,owner=runner(a);v.resource.context.generation=2;self.assertIsNone(S.read_control_comparison(v))
  v,owner=runner(SimpleNamespace());self.assertIsNone(S.read_control_comparison(v))
 def test_client_real_bounded_socket_method_fake_transport_no_retry(self):
  records=[]
  class Wire:
   def __init__(self,payload):self.pending=payload
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def settimeout(self,x):pass
   def connect(self,path):records.append(('connect',path))
   def sendall(self,x):records.append(('send',x))
   def recv(self,n):records.append(('recv_cap',n));r=self.pending[:n];self.pending=self.pending[n:];return r
  good=dict(ok=True,schema='ubts.native_observation.local_reply.v1',state='active',comparison=compact(row()),runtime_authority=False,motor_command_sent=False,autonomy_output_enabled=False)
  client=C.NativeObservationClient('/synthetic-only.sock')
  client._request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('legacy fallback'))
  malformed=copy.deepcopy(good);malformed['comparison']['scan']['ranges']=[]
  for payload,okay in ((json.dumps(good).encode()+b'\n',True),(json.dumps(malformed).encode()+b'\n',False),
    (b'{"ok":false}\n',False),(b'x'*4097+b'\n',False),(b'{}\n{}\n',False),(b'{"ok":NaN}\n',False),
    (b'{}',False),(b'{"ok":false,"ok":true}\n',False),(b'['*1100+b'0'+b']'*1100+b'\n',False)):
   records.clear()
   with patch.object(C.socket,'socket',lambda *a:Wire(payload)),patch.object(C.socket,'AF_UNIX',1,create=True):result=client.control_comparison()
   self.assertEqual(result is not None,okay);self.assertEqual(sum(k=='send'for k,*_ in records),1)
   self.assertTrue(all(v<=4096 for k,v in records if k=='recv_cap'))
 def test_application_default_off_and_protocol_is_explicit_no_auto_fallback(self):
  text=(K/'overlays/dashboard_server/app.py').read_text(encoding='utf-8')
  self.assertIn("os.environ.get('UBTS_NATIVE_CONTROL_COMPARISON_V1','0')",text)
  self.assertIn("if comparison_protocol=='1' else None",text)
  self.assertIn('native_comparison_protocol_requires_native_source',text)

if __name__=='__main__':
 start=time.monotonic();result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(ComparisonTests))
 print(json.dumps(dict(ok=result.wasSuccessful(),tests=result.testsRun,errors=len(result.errors),failures=len(result.failures),skipped=len(result.skipped),actual_IO=False,SDK_ROS_threads_or_motors=False,wall_s=time.monotonic()-start)))
 raise SystemExit(0 if result.wasSuccessful()else 1)
