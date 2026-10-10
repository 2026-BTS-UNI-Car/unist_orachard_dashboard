"""Synthetic physical declarations + real planner/FF-P/manual core; no hardware IO."""
import ast,copy,hashlib,importlib.util,sys,threading,time,unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse
HERE=Path(__file__).resolve().parents[1]
REPO=Path(sys.argv.pop(sys.argv.index('--repo')+1)) if '--repo' in sys.argv else HERE
if '--repo' in sys.argv:sys.argv.remove('--repo')
sys.path[:0]=[str(REPO),str(REPO/'tests')]
import dashboard_server
dashboard_server.__path__.insert(0,str(HERE/'dashboard_server'))
from dashboard_server.native_autonomy_supervisor import *
from dashboard_server.native_autonomy_application import NativeAutonomyApplication,TrustedPhysicalBindings
from dashboard_server.record_based_autonomy import RecordBasedAutonomy
from scripts.rover_control_core import RoverControl,ControlError
from test_record_based_autonomy import native_snapshot,scene,CONTEXT

class Motor:
    ready=True;firmware='synthetic-only';error=None;stopped=True
    def __init__(self):self.outputs={'left':0,'right':0};self.drive_calls=[];self.stops=0
    def hard_stop(self):self.outputs={'left':0,'right':0};self.stopped=True;self.stops+=1
    def enable(self):pass
    def drive(self,left,right):self.outputs={'left':left,'right':right};self.drive_calls.append((left,right));self.stopped=not(left or right)
    def release(self):self.hard_stop()

class Rig:
    def __init__(self,enabled=True):
        self.now=1.;self.requests=[];self.input_calls=0;self.auth=True
        self.s=native_snapshot(0);self.s['context']['epoch']='synthetic-epoch'
        self.context=NativeContext(CONTEXT['source_session'],'synthetic-epoch',1,CONTEXT['source_clock_domain'],CONTEXT['receipt_clock_domain'],'synthetic-calibration')
        self.profile=MeasuredProfile(calibration_id=self.context.calibration_id,measured=True,braking_measured=True,
            linear_ff=80.,yaw_ff=2.,linear_kp=40.,yaw_kp=20.,max_forward_m_s=.2,max_feedback_m_s=.3,
            max_yaw_rad_s=.2,max_lateral_m_s=.05,max_percent=20.,slew_percent_per_s=100.,
            braking_test_speed_m_s=.3,stop_distance_m=.4,stop_time_s=1.)
        self.timing=TimingLimits(200_000_000,200_000_000,250_000_000)
        self.evidence=ActivationEvidence(self.context,True,True,True,True,True,True,True,
            'synthetic-body','synthetic-braking','synthetic-estop')
        self.motor=Motor();self.core=RoverControl(self.motor,clock=lambda:self.now)
        self.supervisor=AutonomySupervisor(enabled=enabled,input_provider=self.input,evidence_provider=lambda s:self.evidence,
            control_provider=self.frame,authorize=lambda session,phase:self.auth,request=self.request,
            profile=self.profile,timing=self.timing,percent_cap=5,clock=lambda:self.now)
    def input(self):self.input_calls+=1;return copy.deepcopy(self.s)
    def frame(self,s,decision,profile):
        stamp=s['scan']['stamp_ns'];receipt=s['scan']['receipt_ns']
        f=NativeFeedback(self.context,stamp,receipt,.1,0.,0.,True)
        space=SweptSpace(self.context,stamp,receipt,receipt+1_000_000,'CLEAR',True,.1,0.,1.)
        return MeasuredControlFrame(f,space,.1,0.)
    def request(self,payload):
        self.requests.append(copy.deepcopy(payload))
        try:return self.core.dispatch(payload)
        except ControlError as error:return dict(ok=False,error=str(error))
    def set(self,index,**opts):
        self.s=native_snapshot(index,**opts);self.s['context']['epoch']='synthetic-epoch';self.now=1.+index*.1
    def start(self):return self.supervisor.start('synthetic-local-session',explicit=True,cruise_percent=15)
    def drive(self):
        self.start()
        for i in range(4):self.set(i);self.supervisor.tick()
        assert self.motor.drive_calls,self.supervisor.status()
        return self

class SupervisorTests(unittest.TestCase):
    def test_default_OFF_no_source_control_or_thread_IO(self):
        r=Rig(False);value=r.start();self.assertFalse(value['active']);self.assertEqual(value['reason'],'default_OFF')
        self.assertEqual(r.requests,[]);self.assertEqual(r.input_calls,0)
        self.assertTrue(value['control_path_implemented']);self.assertFalse(value['physical_autonomy_qualified'])
    def test_missing_actual_BODY_braking_and_each_preflight_flag_blocks_before_arm(self):
        r=Rig();r.supervisor.preview.profile=None
        self.assertIn('measured_BODY',r.start()['reason']);self.assertEqual(r.requests,[])
        for key in ('body_mount_verified','native_body_velocity_validated','braking_verified','emergency_stop_verified','source_pipeline_validated','swept_provider_validated','motor_configuration_reviewed'):
            r=Rig();r.evidence=replace(r.evidence,**{key:False});out=r.start()
            self.assertFalse(out['active']);self.assertIn(key,out['reason']);self.assertEqual(r.requests,[])
    def test_real_native_planner_then_measured_FFP_firstzero_owned_manual_core_cap(self):
        r=Rig();r.start()
        for i in range(3):r.set(i);r.supervisor.tick()
        self.assertFalse(r.motor.drive_calls);self.assertTrue(r.core.lease)
        r.set(3);s=r.supervisor.tick();self.assertEqual(r.motor.drive_calls,[(5,5)])
        self.assertEqual(s['requested_left_percent'],5);self.assertFalse(s['physical_autonomy_qualified'])
        drives=[q for q in r.requests if q['op']=='drive'];self.assertEqual(drives[0]['seq'],1)
        self.assertIs(drives[0]['deadman'],True);self.assertNotIn('keepalive',[q['op'] for q in r.requests])
    def test_estimated_row_percents_not_sent_as_measured_target_or_PWM(self):
        r=Rig().drive();decision=r.supervisor.status()['current_decision']
        self.assertNotEqual(decision['proposed_left_percent'],5);self.assertEqual(r.motor.drive_calls[-1],(5,5))
        self.assertFalse(decision['execution_eligible']);self.assertFalse(decision['estimates']['braking_verified'])
    def test_optional_cap_inherits_measured_profile_no_invented_twenty_clamp(self):
        r=Rig();r.profile=replace(r.profile,max_percent=65.)
        r.supervisor.preview.profile=r.profile;r.supervisor.percent_cap=55.
        state=r.start();self.assertTrue(state['active']);self.assertEqual(state['effective_percent_cap'],55.)
        r.supervisor.stop();r.supervisor.percent_cap=None
        state=r.start();self.assertTrue(state['active']);self.assertEqual(state['effective_percent_cap'],65.)
        r.supervisor.stop();r.supervisor.percent_cap=66.
        self.assertIn('within_measured_profile',r.start()['reason']);self.assertEqual(r.requests,[])
    def test_Stop_or_manual_during_blocked_drive_priority_without_step_or_RPC_wait(self):
        for manual in (False,True):
            r=Rig().drive();r.set(4);entered=threading.Event();release=threading.Event();stop_seen=threading.Event()
            request=r.request;commands=len(r.motor.drive_calls)
            def delayed(q):
                if q['op']=='drive':entered.set();self.assertTrue(release.wait(2))
                if q['op']=='stop':stop_seen.set();return dict(ok=True,state='stop_requested')
                return request(q)
            r.supervisor.request=delayed;t=threading.Thread(target=r.supervisor.tick);t.start()
            self.assertTrue(entered.wait(2));began=time.monotonic()
            out=r.supervisor.manual_takeover() if manual else r.supervisor.stop()
            self.assertLess(time.monotonic()-began,.5);self.assertTrue(stop_seen.is_set())
            self.assertFalse(out['active']);self.assertFalse(out['stop_ack']['physical_zero_verified'])
            self.assertTrue(out['hardware_stop_pending_or_unknown']);reason=out['reason']
            release.set();t.join(2);self.assertFalse(t.is_alive())
            self.assertEqual(r.supervisor.status()['reason'],reason)
            request(dict(op='stop'));self.assertEqual(r.motor.outputs,{'left':0,'right':0})
            # The already entered drive may finish, but no further drive/renew is issued.
            before=len(r.requests);r.supervisor.tick();self.assertEqual(len(r.requests),before)
    def test_unresolved_priority_Stop_holds_owner_and_zero_proposal_until_resolved(self):
        r=Rig().drive();entered=threading.Event();release=threading.Event();request=r.request
        def delayed(q):
            if q['op']=='stop':entered.set();self.assertTrue(release.wait(2))
            return request(q)
        r.supervisor.request=delayed;t=threading.Thread(target=r.supervisor.stop);t.start()
        self.assertTrue(entered.wait(2));state=r.supervisor.status()
        self.assertFalse(state['active']);self.assertTrue(state['owner_cleanup_pending'])
        self.assertEqual(state['current_decision']['decision'],'STOP')
        self.assertEqual(state['current_decision']['proposed_left_percent'],0.)
        with self.assertRaisesRegex(ValueError,'cleanup_pending'):r.start()
        release.set();t.join(2);self.assertFalse(t.is_alive())
        self.assertFalse(r.supervisor.status()['owner_cleanup_pending']);self.assertEqual(r.motor.outputs,{'left':0,'right':0})
        self.assertTrue(r.start()['active']);r.supervisor.stop()
    def test_arm_RPC_age_expiry_before_next_drive_cannot_send_stale_command(self):
        r=Rig();r.start();request=r.request
        def ages(q):
            answer=request(q)
            if q['op']=='arm':r.s['receipt_now_ns']+=RAW_TTL_NS
            return answer
        r.supervisor.request=ages
        for i in range(3):r.set(i);r.supervisor.tick()
        self.assertFalse(r.supervisor.status()['active']);self.assertFalse(r.motor.drive_calls)
        self.assertEqual(r.requests[-1]['op'],'stop');self.assertIsNone(r.core.lease)
    def test_unknown_or_wall_geometry_never_arms(self):
        for options in ({'raw_scene':scene(left=False)},{'raw_scene':scene(wall=.2)}):
            r=Rig();r.start()
            for i in range(4):r.set(i,**options);r.supervisor.tick()
            self.assertEqual(r.requests,[]);self.assertFalse(r.motor.drive_calls)
    def test_Stop_nativeWAIT_manual_takeover_or_auth_loss_priority_zero_revoke(self):
        for reason in ('stop','none','manual','auth'):
            r=Rig().drive()
            if reason=='stop':out=r.supervisor.stop()
            elif reason=='manual':out=r.supervisor.manual_takeover()
            elif reason=='none':r.s=None;out=r.supervisor.tick()
            else:r.auth=False;out=r.supervisor.tick()
            self.assertFalse(out['active']);self.assertIsNone(r.core.lease);self.assertEqual(r.motor.outputs,{'left':0,'right':0})
            self.assertEqual(r.requests[-1]['op'],'stop');self.assertFalse(out['stop_ack']['physical_zero_verified'])
    def test_same_fresh_input_no_drive_or_input_deadman_extension(self):
        r=Rig().drive();count=len(r.requests);deadline=r.core.input_until
        r.supervisor.tick();self.assertEqual(len(r.requests),count);self.assertEqual(r.core.input_until,deadline)
        self.assertEqual(r.supervisor.status()['counters']['duplicates'],1)
    def test_stale_future_context_order_and_same_sequence_mutation_stop(self):
        for kind in ('stale','future','context','sequence','mutation'):
            r=Rig().drive()
            if kind=='stale':r.s['receipt_now_ns']=r.s['imu']['receipt_ns']+RAW_TTL_NS
            elif kind=='future':r.s['receipt_now_ns']=r.s['scan']['receipt_ns']-1
            elif kind=='context':r.s['context']['generation']=2
            elif kind=='sequence':r.s['scan']['sequence']=1
            else:r.s['scan']['ranges'][0]+=.1
            out=r.supervisor.tick();self.assertFalse(out['active']);self.assertEqual(r.motor.outputs,{'left':0,'right':0})
    def test_postplanning_expiry_does_not_send_old_drive(self):
        r=Rig().drive();r.set(4);old=len(r.motor.drive_calls);plan=r.supervisor.planner.plan
        def slow(*args,**kwargs):
            result=plan(*args,**kwargs);r.s['receipt_now_ns']+=RAW_TTL_NS;return result
        r.supervisor.planner.plan=slow;out=r.supervisor.tick()
        self.assertFalse(out['active']);self.assertEqual(len(r.motor.drive_calls),old);self.assertEqual(r.motor.outputs,{'left':0,'right':0})
    def test_invalid_swept_BODY_endpoint_or_profile_change_never_nonzero(self):
        for kind in ('unknown','endpoint','target','profile'):
            r=Rig();r.start();frame=r.frame
            def wrong(s,d,p):
                a=frame(s,d,p)
                if kind=='unknown':return replace(a,swept=replace(a.swept,body_geometry_verified=False))
                if kind=='endpoint':return replace(a,feedback=replace(a.feedback,receipt_ns=a.feedback.receipt_ns-1))
                if kind=='target':return replace(a,desired_forward_m_s=.12)
                return a
            r.supervisor.control_provider=wrong
            for i in range(3):
                r.set(i)
                if kind=='profile' and i==2:r.supervisor.preview.profile=replace(r.profile,linear_ff=81.)
                r.supervisor.tick()
            self.assertFalse(r.motor.drive_calls);self.assertIsNone(r.core.lease)
    def test_lease_ticket_rejection_and_invalid_arm_reply_Stop_not_fakecleanup(self):
        r=Rig().drive();r.supervisor._lease['ticket']='bad';r.set(4);out=r.supervisor.tick()
        self.assertFalse(out['active']);self.assertEqual(r.motor.outputs,{'left':0,'right':0})
        r=Rig();original=r.request
        def malformed(q):
            out=original(q)
            if q['op']=='arm':out.pop('ticket')
            return out
        r.supervisor.request=malformed;r.start()
        for i in range(3):r.set(i);r.supervisor.tick()
        self.assertIsNone(r.core.lease);self.assertEqual(r.requests[-1]['op'],'stop')
    def test_Stop_during_inflight_arm_late_reply_cannot_publish_or_leave_lease(self):
        r=Rig();r.start()
        for i in range(2):r.set(i);r.supervisor.tick()
        entered=threading.Event();release=threading.Event();request=r.request
        def delayed(q):
            if q['op']=='arm':entered.set();self.assertTrue(release.wait(2))
            return request(q)
        r.supervisor.request=delayed;r.set(2);t=threading.Thread(target=r.supervisor.tick);t.start()
        self.assertTrue(entered.wait(2));r.supervisor.stop()
        with self.assertRaisesRegex(ValueError,'cleanup_pending'):r.start()
        release.set();t.join(2);self.assertFalse(t.is_alive());self.assertIsNone(r.core.lease)
        self.assertFalse(r.motor.drive_calls);self.assertFalse(r.supervisor.status()['active'])
    def test_concurrent_Start_dedup_and_Stop_during_preflight_newowner_blocked(self):
        r=Rig();entered=threading.Event();release=threading.Event()
        def evidence(s):entered.set();self.assertTrue(release.wait(2));return r.evidence
        r.supervisor.evidence_provider=evidence;t=threading.Thread(target=r.start);t.start();self.assertTrue(entered.wait(2))
        self.assertEqual(r.start()['state'],'PREPARING');self.assertEqual(r.input_calls,1)
        with self.assertRaisesRegex(ValueError,'single_operator'):r.supervisor.start('another',explicit=True)
        r.supervisor.stop()
        with self.assertRaisesRegex(ValueError,'cleanup_pending'):r.start()
        release.set();t.join(2);self.assertFalse(t.is_alive());self.assertEqual(r.requests,[])
    def test_true_poll_loop_reads_source_and_exits_zero(self):
        r=Rig();r.start();exit_event=threading.Event();t=threading.Thread(target=r.supervisor.run,args=(exit_event,));t.start()
        end=time.monotonic()+1
        while r.input_calls<4 and time.monotonic()<end:time.sleep(.005)
        exit_event.set();t.join(2);self.assertFalse(t.is_alive());self.assertGreaterEqual(r.input_calls,4)
        self.assertFalse(r.supervisor.status()['active']);self.assertFalse(r.motor.drive_calls)
    def test_loop_fault_exits_without_automatic_restart(self):
        r=Rig();r.start();r.s['receipt_now_ns']=r.s['scan']['receipt_ns']-1
        exit_event=threading.Event();t=threading.Thread(target=r.supervisor.run,args=(exit_event,));t.start();t.join(1)
        self.assertFalse(t.is_alive());self.assertFalse(r.supervisor.status()['active'])
        self.assertIn('stale_or_future',r.supervisor.status()['reason']);self.assertEqual(r.requests,[])
    def test_bounded_status_and_no_lease_ticket_or_originalscan_leak(self):
        r=Rig().drive()
        for _ in range(200):r.supervisor._event('bounded')
        out=r.supervisor.status();self.assertEqual(len(out['evidence']),128)
        import json
        raw=json.dumps(out);self.assertNotIn(r.core.lease,raw);self.assertNotIn(r.core.ticket,raw)
        self.assertNotIn('ranges',raw);self.assertNotIn('synthetic-local-session',raw)

class AppBindingTests(unittest.TestCase):
    def test_default_binding_no_native_or_rover_IO_no_thread(self):
        class Native:
            def input(self):raise AssertionError('default OFF read')
        app=NativeAutonomyApplication(Native(),request=lambda q:(_ for _ in ()).throw(AssertionError('default OFF wire')))
        out=app.start('test-session',explicit=True,cruise_percent=15)
        self.assertFalse(out['active']);self.assertEqual(out['reason'],'default_OFF');self.assertFalse(out['loop_alive'])
        self.assertFalse(out['loop_cleanup_pending'])
        self.assertEqual(out['counters'],dict(inputs=0,plans=0,commands=0,duplicates=0,stops=0,errors=0))
        self.assertTrue(app.close()['loop_joined'])
    def test_active_duplicate_Start_returns_application_loop_fields(self):
        r=Rig();bindings=TrustedPhysicalBindings(r.profile,r.timing,lambda s:r.evidence,r.frame,lambda session,phase:True,5.)
        native=type('Native',(),{'input':lambda _self:r.input()})()
        app=NativeAutonomyApplication(native,session_valid=lambda session:True,bindings=bindings,request=r.request)
        try:
            first=app.start('synthetic-local-session',explicit=True,cruise_percent=15)
            duplicate=app.start('synthetic-local-session',explicit=True,cruise_percent=15)
            self.assertTrue(first['active']);self.assertEqual(first['generation'],duplicate['generation'])
            self.assertTrue(duplicate['loop_alive']);self.assertTrue(duplicate['loop_cleanup_pending'])
            self.assertTrue(duplicate['physical_bindings_configured'])
            self.assertEqual(r.requests,[])
        finally:self.assertTrue(app.close()['loop_joined'])
    def test_Stop_during_preflight_keeps_pending_until_Start_attempt_resolves(self):
        r=Rig();entered=threading.Event();release=threading.Event()
        def evidence(s):entered.set();self.assertTrue(release.wait(2));return r.evidence
        bindings=TrustedPhysicalBindings(r.profile,r.timing,evidence,r.frame,lambda session,phase:True,5.)
        native=type('Native',(),{'input':lambda _self:r.input()})()
        app=NativeAutonomyApplication(native,session_valid=lambda session:True,bindings=bindings,request=r.request)
        answers=[];t=threading.Thread(target=lambda:answers.append(app.start('synthetic-local-session',explicit=True,cruise_percent=15)))
        t.start();self.assertTrue(entered.wait(2));stopped=app.stop()
        self.assertFalse(stopped['loop_joined']);self.assertTrue(stopped['loop_cleanup_pending'])
        with self.assertRaisesRegex(ValueError,'cleanup_pending'):app.start('synthetic-local-session',explicit=True,cruise_percent=15)
        release.set();t.join(2);self.assertFalse(t.is_alive())
        self.assertFalse(answers[0]['active']);self.assertFalse(answers[0]['loop_cleanup_pending']);self.assertFalse(answers[0]['loop_alive'])
        self.assertEqual(r.requests,[]);self.assertTrue(app.close()['loop_joined'])
    def test_real_app_method_local_origin_session_explicitpayload_then_specific_block(self):
        tree=ast.parse((HERE/'dashboard_server/app.py').read_bytes())
        handler=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='DashboardHandler')
        method=next(x for x in handler.body if isinstance(x,ast.FunctionDef) and x.name=='_native_autonomy_action')
        namespace={'urlparse':urlparse};exec(compile(ast.Module(body=[method],type_ignores=[]),'app-method','exec'),namespace)
        class Handler:
            headers={'Origin':'http://127.0.0.1:8080','Host':'127.0.0.1:8080'};client_address=('127.0.0.1',1)
            autonomy_supervisor=NativeAutonomyApplication()
            autonomy_settings=type('Settings',(),{'percent':15})()
            def _ensure_session(self,create):return 'test-session'
        h=Handler();call=namespace['_native_autonomy_action']
        out=call(h,'start',{'operator_arm':True,'deadman':True})
        self.assertTrue(out['control_path_implemented']);self.assertFalse(out['active']);self.assertEqual(out['reason'],'default_OFF')
        for payload in ({},{'operator_arm':True},{'operator_arm':True,'deadman':1},{'operator_arm':True,'deadman':True,'scan':{}}):
            with self.assertRaises(ValueError):call(h,'start',payload)
        h.client_address=('10.0.0.1',1)
        with self.assertRaisesRegex(ValueError,'local'):call(h,'start',{'operator_arm':True,'deadman':True})
    def test_original_manual_Stop_forwards_even_when_supervisor_cleanup_unknown(self):
        from unittest.mock import patch
        tree=ast.parse((HERE/'dashboard_server/app.py').read_bytes())
        handler=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='DashboardHandler')
        method=next(x for x in handler.body if isinstance(x,ast.FunctionDef) and x.name=='_rover_action')
        namespace={'urlparse':urlparse};exec(compile(ast.Module(body=[method],type_ignores=[]),'app-manual','exec'),namespace)
        class Supervisor:
            def status(self):return dict(active=False,loop_cleanup_pending=True)
            def manual_takeover(self):return dict(loop_joined=False)
        class Handler:
            headers={'Origin':'http://127.0.0.1:8080','Host':'127.0.0.1:8080'};autonomy_supervisor=Supervisor()
            def __init__(self):self.sent=[]
            def _ensure_session(self,create):return 'manual-session'
            def _read_json_body(self):return {}
            def _send_json(self,value,status=200):self.sent.append((value,status))
            def send_error(self,*args):raise AssertionError(args)
        call=namespace['_rover_action'];h=Handler()
        with patch('dashboard_server.rover_api.request',return_value=dict(ok=True,state='stop_requested')) as request:
            call(h,'/api/rover/stop');request.assert_called_once_with(dict(op='stop',owner='manual-session'))
            self.assertEqual(h.sent[-1][1],200);request.reset_mock()
            call(h,'/api/rover/arm');request.assert_not_called();self.assertEqual(h.sent[-1][1],409)
    def test_app_exposes_supervisor_status_and_manual_handoff_cleanup(self):
        text=(HERE/'dashboard_server/app.py').read_text(encoding='utf-8')
        self.assertIn("'/api/autonomy/supervisor'",text);self.assertIn('autonomy_manual_handoff_cleanup_pending',text)
        self.assertIn('bindings=autonomy_bindings',text);self.assertIn('supervisor.close()',text)
        self.assertNotIn("{**self.autonomy_settings.status(),'ok':False},409",text)

if __name__=='__main__':unittest.main()
