"""Bounded synthetic lock seams; no serial/ROS/host time/calibration IO."""
import threading

class ObservedLock:
    def __init__(self,watched,attempt):self.inner=threading.RLock();self.watched=watched;self.attempt=attempt
    def __enter__(self):
        if threading.current_thread().name==self.watched:self.attempt.set()
        self.inner.acquire();return self
    def __exit__(self,*_):self.inner.release()

def commit_wins(cls,context,first,second):
    a=cls(context,enabled=True,validate_original=lambda _:True)
    assert a.admit(first)['accepted'];prefix=list(a.records)
    append_entered=threading.Event();append_release=threading.Event();stop_attempt=threading.Event();stop_return=threading.Event()
    trace=[];out={};errors=[]
    a.lock=ObservedLock('UTC-seam-Stop',stop_attempt)
    class GatedList(list):
        def append(self,record):
            append_entered.set();assert append_release.wait(3),'bounded append seam timed out'
            out['append_observed_stopped_event']=a.stop_event.is_set()
            super().append(record);trace.append('append')
    a.records=GatedList(a.records)
    def admission():
        try:out['admission']=a.admit(second)
        except BaseException as e:errors.append(repr(e))
    def stopping():
        try:
            out['Stop_status']=a.stop();trace.append('Stop_return');out['records_at_Stop_return']=len(a.records);stop_return.set()
        except BaseException as e:errors.append(repr(e))
    t=threading.Thread(target=admission,name='UTC-seam-commit');s=threading.Thread(target=stopping,name='UTC-seam-Stop')
    t.start()
    try:
        assert append_entered.wait(2),'admission never reached commit seam'
        s.start();assert stop_attempt.wait(2),'Stop never attempted commit lock'
        out['event_set_while_commit_lock_owned']=a.stop_event.is_set()
        out['Stop_returned_before_release']=stop_return.is_set()
    finally:
        append_release.set();t.join(3)
        if s.ident is not None:s.join(3)
    assert not errors,errors
    assert 'admission' in out and 'Stop_status' in out,out
    out.update(trace=trace,admit_accepted=out.pop('admission')['accepted'],prefix_retained=list(a.records[:1])==prefix,
       records_after_Stop_return=len(a.records),late_admission_accepted=a.admit(second)['accepted'],
       all_threads_joined=not t.is_alive() and not s.is_alive())
    return out

def stop_wins(cls,context,first,second):
    a=cls(context,enabled=True,validate_original=lambda _:True)
    assert a.admit(first)['accepted'];prefix=list(a.records)
    attempt=threading.Event();returned=threading.Event();out={};errors=[]
    a.lock=ObservedLock('UTC-seam-queued-admission',attempt)
    def admission():
        try:out['admission']=a.admit(second);returned.set()
        except BaseException as e:errors.append(repr(e))
    t=threading.Thread(target=admission,name='UTC-seam-queued-admission')
    try:
        with a.lock:
            t.start();assert attempt.wait(2),'queued admission did not attempt commit lock'
            out['Stop_status']=a.stop();out['records_at_Stop_return']=len(a.records)
            out['Stop_returned_before_queued_admission']=not returned.is_set()
    finally:t.join(3)
    assert not errors,errors
    assert 'admission' in out,out
    result=out.pop('admission')
    out.update(queued_admission_accepted=result['accepted'],reason=result['reason'],prefix_retained=list(a.records)==prefix,
        records_after_Stop_return=len(a.records),all_threads_joined=not t.is_alive())
    return out
