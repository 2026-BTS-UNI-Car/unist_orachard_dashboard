"""Optional original GPS callback dispatcher. Never enters physical ingress.

GPS errors/NoFix are diagnostic exclusions, not scan/IMU startup failures. The
outer original NDJSON sequence and owned observer BEGIN remain mandatory.
Only bounded raw pairs are retained; no DDS/clock/process creation here.
"""
from collections import Counter
import hashlib
import json
import threading
from native_gnss_fusion import LiveGPSPairs,solution,require

MAX_PAIRS=256
MAX_PAIR_BYTES=4*1024*1024


class NativeGNSSDispatch:
    def __init__(self,map_adapter,expected_gids):
        require(type(expected_gids) is dict and set(expected_gids)=={'/gps/data','/gps/fix'},'exact_optional_GPS_GIDs_required')
        self.map=map_adapter;self.gids=dict(expected_gids);self.cancel=threading.Event();self.lock=threading.RLock()
        self.begin=None;self.collector=None;self.originals=[];self.byte_count=0
        self.counts=Counter();self.reason=None;self.last_raw=self.last_system=None

    def bind_begin(self,row):
        with self.lock:
            require(self.begin is None and row.get('optional_GNSS_enabled') is True and
                row.get('expected_GPS_data_GID')==self.gids['/gps/data'] and
                row.get('expected_GPS_fix_GID')==self.gids['/gps/fix'],'original_GPS_begin_binding_required')
            self.begin=dict(row)
            ctx=self.map._context
            self.collector=LiveGPSPairs(ctx,self.gids,process_validator=self._validate_process)

    def _validate_process(self,row):
        b=self.begin
        return (b is not None and row.get('observer_pid')==b['pid'] and
            row.get('observer_process_session')==b['observer_process_session'] and
            row.get('time_namespace')==b['time_namespace'] and
            row.get('RAW_schema')=='linux.clock_monotonic_raw.integer_ns.v1' and
            row.get('rmw_implementation')==b['rmw_implementation'])

    def stop(self):
        self.cancel.set()
        with self.lock:
            if self.collector is not None:self.collector.stop()

    def receive(self,row):
        with self.lock:
            if self.cancel.is_set():self.counts['after_stop']+=1;return ()
            try:
                require(self.collector is not None,'GPS_begin_missing')
                if row.get('kind')=='gnss_unavailable':
                    self.reason=str(row.get('error','GPS_unavailable'))[:128];self.counts['excluded']+=1;return ()
                require(row.get('kind')=='original_gnss_cdr','original_GPS_row_required')
                raw,wall=row.get('callback_monotonic_raw_ns'),row.get('observer_system_ros_now_ns')
                require(type(raw) is int and type(wall) is int and 0<=raw<2**63 and 0<=wall<2**63 and
                    (self.last_raw is None or raw>=self.last_raw) and
                    (self.last_system is None or wall>=self.last_system),'original_GPS_callback_clock_rewind')
                original=self.collector.receive_row(row)
                self.last_raw,self.last_system=raw,wall;self.counts['original_rows']+=1
                if original is None:return ()
                try:s,reason=solution(original)
                except ValueError as error:
                    self.reason=str(error)[:128];self.counts['quality_excluded']+=1;return ()
                if s is None:self.reason=reason;self.counts[reason]+=1;return ()
                n=len(original.data_cdr)+len(original.fix_cdr)
                if len(self.originals)>=MAX_PAIRS or self.byte_count+n>MAX_PAIR_BYTES:
                    self.map.pose_ledger.mark_gnss_capacity()
                    self.reason='retained_original_GPS_capacity';self.counts['capacity']+=1;return ()
                retained=self.map.pose_ledger.append_gnss(original,validate=lambda:
                    not self.cancel.is_set() and self.map._context==original.context and
                    self.collector.context==original.context)
                if not retained:
                    self.reason='retained_original_GPS_capacity_or_stopped';self.counts['capacity']+=1;return ()
                self.originals.append(original);self.byte_count+=n;self.counts['quality_pairs']+=1
                self.reason=None
            except (ValueError,KeyError,TypeError,UnicodeError) as error:
                self.reason=str(error)[:128];self.counts['excluded']+=1
                # An identity/clock/corrupt row never earns GNSS admission.
                # Preserve the physical worker; explicit source restart is needed
                # to replace a failed collector's temporal identity.
                if self.collector is not None:self.collector.stop()
            return ()

    def status(self):
        with self.lock:return dict(enabled=True,quality_pairs=len(self.originals),original_bytes=self.byte_count,
            pending_limit=16,pairs_limit=MAX_PAIRS,reason=self.reason,counts=dict(self.counts),
            stopped=self.cancel.is_set(),native_GNSS_pipeline_qualified=False,
            fused_occupancy_qualified=False,runtime_authority=False,motor_command_sent=False)
