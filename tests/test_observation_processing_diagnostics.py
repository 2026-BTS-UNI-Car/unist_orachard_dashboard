"""Exact deployed-method synthetic regressions; no IO, thread, clock spoof or motor."""
from pathlib import Path
from types import SimpleNamespace
import ast,hashlib,json,threading,time,copy
U=Path('D:/Develop/UBTS');S=Path(__file__).resolve().parents[1]/'dashboard_server/autonomy_observation.py'
PIN='451b786b983f69d596a74525d71fbe45c789466d8cb530144f2bee024a0757e3'
def load():
 raw=S.read_bytes();assert PIN is None or hashlib.sha256(raw).hexdigest()==PIN
 tree=ast.parse(raw);tree.body=[n for n in tree.body if not(isinstance(n,ast.ImportFrom)and n.module=='dashboard_server.native_owner_lifecycle')]
 env={};exec(compile(tree,str(S),'exec'),env);return env
def row(seq=1,age=1000000):
 stamp=1000000000+seq*100000000;receipt=100000000000+seq*100000000
 return dict(context=dict(source_session='generated-only-source',generation=1,source_clock_domain='SIM_SOURCE',receipt_clock_domain='SIM_RECEIPT'),
  source_now_ns=stamp+age,receipt_now_ns=receipt+age,
  scan=dict(sequence=seq,frame_id='laser',stamp_ns=stamp,receipt_ns=receipt,ranges=[2.]*24),
  imu=dict(stamp_ns=stamp-1000000,receipt_ns=receipt-1000000),
  motion=dict(previous_scan_stamp_ns=stamp-100000000,current_scan_stamp_ns=stamp,receipt_ns=receipt))
def fixture(env,provider,action=None):
 owner=SimpleNamespace(latest=SimpleNamespace(diagnostic_json=json.dumps(env['zero_decision']('WAIT_INPUT'))),latest_stale=False,owned=True,cancelled=False,first_failure_reason=None,state='active',session='synthetic',generation=1,retained_records=1,retained_bytes=1,evicted_records=0)
 life=SimpleNamespace(status=lambda:owner)
 context=SimpleNamespace(cancelled=False,published=[])
 def publish(value,evidence):
  context.published.append((copy.deepcopy(value),copy.deepcopy(evidence)));owner.latest=SimpleNamespace(diagnostic_json=json.dumps(value));return True
 context.publish=publish
 parent=SimpleNamespace(lock=threading.RLock(),planner=None,input_provider=provider,clock=None,input_age_ns=250000000,cruise_percent=10.,preference_version=0,lifecycle=life)
 planner=SimpleNamespace(processed=0,reasons_during_plan=[])
 def plan(value,cruise_percent):
  planner.processed+=1;planner.reasons_during_plan.append(env['AutonomyObservation'].status(parent)['current_decision']['reason'])
  if action:action(parent)
  answer=env['zero_decision']('FIELD10_ADAPTER_UNQUALIFIED');answer['field10']=dict(scans_processed=planner.processed);return answer
 planner.plan=plan;parent.planner=planner;worker=env['_ObservationWorker'](parent,context);parent.resource=worker
 return parent,planner,worker,context
def rejection(fn,needle):
 try:fn()
 except ValueError as e:assert needle in str(e);return
 raise AssertionError('required rejection did not happen')

def run():
 started=time.monotonic();env=load();r=row()
 p,plan,w,c=fixture(env,lambda:None);w.process(r)
 status=env['AutonomyObservation'].status(p);meta=status['processing_diagnostics']
 assert plan.processed==w.processed==1 and w.decisions==0 and w.age is None
 assert c.published[-1][0]['reason']=='WAIT_INPUT'and'field10'not in c.published[-1][0]
 assert plan.reasons_during_plan==['WAIT_RESULT']and p.preference_version==0
 assert meta['last_planner_counts']=={'scans_processed':1}and meta['plans_finished']==1
 assert meta['last_plan_sequence']==1 and meta['last_published_sequence']is None
 assert meta['last_discard_reason']=='POST_PLAN_INPUT_UNAVAILABLE'
 assert meta['provider_unavailable']=={'poll_input':0,'post_plan_input':1}
 assert meta['stage_timing_ns']['post_plan_input']['calls']==1
 assert meta['stage_timing_ns']['planner_and_validation']['calls']==1
 assert meta['clock_role']=='HOST_MONOTONIC_RELATIVE_DURATION_ONLY'and meta['physical_authority']is False
 assert 'ranges'not in json.dumps(meta)and 'position'not in json.dumps(meta)
 r2=row(2);p.input_provider=lambda:copy.deepcopy(r2);w.process(r2)
 assert plan.processed==2 and w.decisions==1 and c.published[-1][0]['field10']['scans_processed']==2
 assert w.last_published_sequence==2 and w.last_discard_reason is None
 assert w.preference_version==0 and p.preference_version==0
 # Independently fresh clock is not enough when the source withholds input.
 p,plan,w,c=fixture(env,lambda:None);p.clock=lambda:dict(source_now_ns=r['source_now_ns'],receipt_now_ns=r['receipt_now_ns'],source_clock_domain='SIM_SOURCE',receipt_clock_domain='SIM_RECEIPT');w.process(r)
 assert w.decisions==0 and c.published[-1][0]['reason']=='WAIT_INPUT'
 # Same exact positive-age and context fences as the original source.
 stale=row(age=260000000);p,plan,w,c=fixture(env,lambda:stale);w.process(r)
 assert w.decisions==0 and c.published[-1][0]['reason']=='WAIT_STALE_INPUT'and w.stale_results==1
 assert w.last_discard_reason=='POST_PLAN_POSITIVE_AGE_EXPIRY'
 different=row();different['context']['generation']=2;p,plan,w,c=fixture(env,lambda:different)
 rejection(lambda:w.process(r),'observation_source_reconnected_during_plan')
 # Once a result was published, a genuine preference version change keeps
 # the old fence AND its original PREFERENCE_CHANGED message.
 p,plan,w,c=fixture(env,lambda:row());w.process(r);assert w.decisions==1
 original=plan.plan
 def changed(value,cruise_percent):
  answer=original(value,cruise_percent);p.preference_version+=1;return answer
 plan.plan=changed;p.input_provider=lambda:row(2);w.process(row(2))
 assert w.decisions==1 and w.late_results==1 and w.last_discard_reason=='PREFERENCE_CHANGED'
 assert env['AutonomyObservation'].status(p)['current_decision']['reason']=='PREFERENCE_CHANGED_WAITING_INPUT'
 # Cancellation after plan still publishes nothing and cannot become a HOLD recovery.
 p,plan,w,c=fixture(env,lambda:row(),lambda x:setattr(x.resource.context,'cancelled',True));w.process(r)
 assert w.decisions==0 and w.late_results==1 and not c.published and w.last_discard_reason=='CANCELLED'
 # Missing IMU creates a WAIT but must not pretend the planner actually ran.
 noimu=row();noimu['imu']=None;p,plan,w,c=fixture(env,lambda:noimu);w.process(noimu)
 assert plan.processed==0 and w.plans_finished==0 and c.published[-1][0]['reason']=='WAIT_IMU'
 assert w.stage_timing['planner_and_validation']['calls']==0
 # Provider failures propagate, with bounded diagnostic timing only.
 p,plan,w,c=fixture(env,lambda:row())
 def fail():raise ValueError('original-provider-failure')
 p.input_provider=fail;rejection(lambda:w.read_input('poll_input'),'original-provider-failure')
 assert w.stage_timing['poll_input']['calls']==1
 w.stage_timing['poll_input']['calls']=env['NS_MAX'];w.record_stage('poll_input',time.monotonic_ns())
 assert w.stage_timing['poll_input']['calls']==env['NS_MAX']
 w.record_stage('poll_input',time.monotonic_ns()+10**12)
 assert w.stage_timing['poll_input']['invalid_samples']==1 and w.stage_timing['poll_input']['last_ns']is None
 return dict(ok=True,generated_groups=10,false_preference_message_fixed=True,true_preference_change_fence_and_message_exact=True,processing_vs_published_counters_distinct=True,metadata_only_timing_bounded=True,source_absence_expiry_context_and_Stop_fail_closed=True,clock_or_TTL_or_source_fields_modified=False,actual_IO_threads_or_sensors=False,wall_s=time.monotonic()-started)

if __name__=='__main__':print(json.dumps(run(),sort_keys=True))
