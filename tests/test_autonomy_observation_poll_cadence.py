"""Actual Worker.run/enqueue/close in real threads; virtual duration only."""
from pathlib import Path
from dashboard_server.original_row_age_budget import OriginalRowAgeBudget
import ast,json,math,queue,threading,types,unittest

ROOT=Path(__file__).resolve().parents[1]
NEW=ROOT/'dashboard_server/autonomy_observation.py'
LEGACY_RUN='def run(self):\n        try:\n            while not self.context.cancelled:\n                if self.parent.input_provider is not None:\n                    supplied = self.read_input("poll_input")\n                    if supplied is not None:\n                        if not self.enqueue(supplied):\n                            break\n                    else:\n                        with self.lock:\n                            old_row, source = self.latest_input, self.source\n                            reason = "WAIT_STALE_INPUT" if self.wait_reason == "WAIT_STALE_INPUT" else "WAIT_INPUT"\n                            if old_row is None:\n                                self.wait_reason, self.phase, self.age = reason, "waiting_input", None\n                        if old_row is not None:\n                            self.wait_input(old_row, source, None, reason=reason)\n                try:\n                    row = self.rows.get_nowait()\n                except queue.Empty:\n                    if self.parent.input_provider is None and self.latest_input is not None:\n                        source, current_age = self.validate(self.latest_input, self.current_clock(self.latest_input))\n                        with self.lock:\n                            self.age = current_age\n                        if self.expired(current_age):\n                            self.wait_input(self.latest_input, source, current_age)\n                    self.wake.wait(self.parent.poll_interval_s)\n                    self.wake.clear()\n                    continue\n                self.process(row)\n                if self.parent.input_provider is not None:\n                    self.wake.clear()\n                    self.context.wait_cancel(self.parent.poll_interval_s)\n        except BaseException as error:\n            self.fail((type(error).__name__ + ": " + str(error))[:128])\n        finally:\n            try:\n                method = getattr(self.parent.planner, "manual_takeover", None) if self.stop_reason == "manual_takeover" else None\n                if callable(method):\n                    method()\n                elif callable(getattr(self.parent.planner, "stop", None)):\n                    self.parent.planner.stop(self.stop_reason or "observation_worker_finished")\n            except BaseException as error:\n                self.fail((type(error).__name__ + ": " + str(error))[:128])'
class FrozenLegacy:
 def read_bytes(self):return NEW.read_bytes()
 def __str__(self):return 'public_parent_run_fd9fd1c.py'
OLD=FrozenLegacy()
class Clock:
 def __init__(self):self.now=0.
 def monotonic(self):return self.now
 def monotonic_ns(self):return int(self.now*1e9)
 def advance(self,seconds):self.now+=seconds
class Context:
 def __init__(self,clock):
  self.clock=clock;self._owner=types.SimpleNamespace(cancel=threading.Event());self.waits=[];self.errors=[];self.stop_after=3
 @property
 def cancelled(self):return self._owner.cancel.is_set()
 def wait_cancel(self,duration):
  assert math.isfinite(duration)and duration>=0
  self.waits.append(duration)
  if not self.cancelled:
   self.clock.advance(duration)
   if len(self.waits)>=self.stop_after:self._owner.cancel.set()
  return self.cancelled
 def fail(self,reason):self.errors.append(reason);self._owner.cancel.set()
class Wake:
 def __init__(self,context):self.context=context;self.calls=[];self.flag=False
 def set(self):self.flag=True
 def clear(self):self.flag=False
 def wait(self,duration):
  self.calls.append(duration)
  if not self.flag:
   self.context.clock.advance(duration)
   if len(self.calls)>=self.context.stop_after:self.context._owner.cancel.set()
  return self.flag
def load(path,clock):
 tree=ast.parse(path.read_bytes())
 if path is OLD:
  target=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='_ObservationWorker')
  target.body=[ast.parse(LEGACY_RUN).body[0]if isinstance(n,ast.FunctionDef)and n.name=='run'else n for n in target.body]
 selected=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))and n.name in('require','bounded_copy','_ObservationWorker')]
 scope=dict(queue=queue,threading=threading,time=clock,json=json,NS_MAX=(1<<63)-1,QUEUE_LIMIT=8,
    OriginalRowAgeBudget=OriginalRowAgeBudget,
    )
 exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),scope)
 return scope['_ObservationWorker']
def fixture(path=NEW,provider_cost=.02,process_cost=.015,empty=False,provider_mode=True):
 clock=Clock();context=Context(clock);starts=[];processed=[];stops=[]
 def provider():starts.append(clock.now);clock.advance(provider_cost);return None if empty else{}
 parent=types.SimpleNamespace(input_provider=provider if provider_mode else None,poll_interval_s=.05,
   planner=types.SimpleNamespace(stop=stops.append),lifecycle=types.SimpleNamespace(_condition=threading.Condition(),_owner=context._owner))
 worker=load(path,clock)(parent,context);worker.wake=Wake(context)
 worker.process=lambda row:(processed.append(clock.now),clock.advance(process_cost))
 return worker,clock,context,starts,processed,stops
def start(worker):
 worker.thread_attempted=True;worker.thread=threading.Thread(target=worker.run,daemon=False);worker.thread.start();return worker.thread
def finish(worker):
 worker.thread.join(.3)
 if worker.thread.is_alive():
  worker.context._owner.cancel.set();raise AssertionError('tiny worker did not terminate')
 if worker.context.errors:raise AssertionError(worker.context.errors)

class CadenceTests(unittest.TestCase):
 def test_same_actual_run_legacy85ms_candidate50ms(self):
  for path,step,wait in[(OLD,.085,.05),(NEW,.05,.015)]:
   w,c,ctx,starts,_,stops=fixture(path);start(w);finish(w)
   for actual,expected in zip(starts,[0,step,2*step]):self.assertAlmostEqual(actual,expected)
   self.assertEqual(len(starts),3)
   for duration in ctx.waits:self.assertAlmostEqual(duration,wait)
   self.assertEqual(len(stops),1)
 def test_empty_provider_budget_and_original_wake_wait(self):
  w,c,ctx,starts,processed,_=fixture(empty=True);start(w);finish(w)
  self.assertEqual(processed,[]);self.assertEqual(len(starts),3);self.assertEqual(ctx.waits,[])
  for duration in w.wake.calls:self.assertAlmostEqual(duration,.03)
  for actual,expected in zip(starts,[0,.05,.1]):self.assertAlmostEqual(actual,expected)
 def test_zero_work_no_busy_spin(self):
  w,c,ctx,starts,_,_=fixture(provider_cost=0,process_cost=0);start(w);finish(w)
  self.assertEqual(ctx.waits,[.05]*3);self.assertEqual(len(starts),3);self.assertAlmostEqual(c.now,.15)
 def test_overrun_no_catchup_or_extra_sleep(self):
  w,c,ctx,starts,_,_=fixture(provider_cost=.04,process_cost=.03);start(w);finish(w)
  self.assertEqual(ctx.waits,[0.]*3)
  for actual,expected in zip(starts,[0,.07,.14]):self.assertAlmostEqual(actual,expected)
 def test_submit_only_wake_semantics_unchanged(self):
  w,c,ctx,_,_,_=fixture(provider_mode=False);start(w);finish(w)
  self.assertEqual(ctx.waits,[]);self.assertEqual(w.wake.calls,[.05]*3)
 def test_stop_race_after_process_does_not_poll_again(self):
  w,c,ctx,starts,_,stops=fixture()
  def process(row):c.advance(.015);ctx._owner.cancel.set()
  w.process=process;start(w);finish(w)
  self.assertEqual(len(starts),1);self.assertEqual(len(stops),1);self.assertAlmostEqual(c.now,.035)
 def test_hung_getter_retains_worker_until_return_no_fake_close(self):
  w,c,ctx,starts,processed,stops=fixture();entered=threading.Event();release=threading.Event()
  def blocked():entered.set();release.wait(.25);return{}
  w.parent.input_provider=blocked;start(w);self.assertTrue(entered.wait(.1))
  try:
   ctx._owner.cancel.set();w.request_stop();w.thread.join(.01)
   self.assertTrue(w.thread.is_alive());self.assertFalse(w.close())
  finally:release.set();finish(w)
  self.assertEqual(processed,[]);self.assertEqual(w.rejected,1);self.assertTrue(w.close());self.assertEqual(len(stops),1)
 def test_provider_fault_keeps_original_fail_and_finally_stop(self):
  w,c,ctx,starts,processed,stops=fixture()
  def failed():raise ValueError('synthetic getter fault')
  w.parent.input_provider=failed;start(w);w.thread.join(.3)
  self.assertFalse(w.thread.is_alive());self.assertTrue(ctx.cancelled)
  self.assertEqual(len(ctx.errors),1);self.assertIn('synthetic getter fault',ctx.errors[0])
  self.assertEqual(processed,[]);self.assertEqual(len(stops),1)

 def test_blocked_empty_provider_public_submit_wakes_same_worker(self):
  w,c,ctx,starts,processed,stops=fixture(empty=True,provider_cost=0)
  w.parent.poll_interval_s=1.  # Long timeout proves wake rather than expiry.
  ctx.session='generated-wake';ctx.generation=1
  ctx._owner.session=ctx.session;ctx._owner.generation=ctx.generation
  w.parent.lock=threading.RLock();w.parent.resource=w
  entered=threading.Event();done=threading.Event();returned=[]
  class BlockingWake(threading.Event):
   def wait(self,timeout=None):
    entered.set();answer=super().wait(timeout);returned.append(answer);return answer
  w.wake=BlockingWake()
  ctx.wait_cancel=lambda timeout:ctx._owner.cancel.wait(timeout)
  def process(row):done.set();ctx._owner.cancel.set()
  w.process=process
  tree=ast.parse(NEW.read_bytes());owner=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='AutonomyObservation')
  method=next(n for n in owner.body if isinstance(n,ast.FunctionDef)and n.name=='submit')
  scope={};exec(compile(ast.Module(body=[method],type_ignores=[]),'actual_public_submit.py','exec'),scope)
  start(w)
  try:
   self.assertTrue(entered.wait(.1),'worker did not enter original wake wait')
   self.assertTrue(scope['submit'](w.parent,{},session=ctx.session,generation=ctx.generation))
   self.assertTrue(done.wait(.1),'enqueue did not wake blocked empty provider')
  finally:
   ctx._owner.cancel.set();w.request_stop();finish(w)
  self.assertEqual(returned,[True]);self.assertEqual(w.received,1);self.assertEqual(len(stops),1)

if __name__=='__main__':unittest.main()
