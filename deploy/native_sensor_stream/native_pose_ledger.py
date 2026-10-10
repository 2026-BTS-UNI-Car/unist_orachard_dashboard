"""Bounded exact committed historical poses/CDR. No clocks, IO or authority.

Appending is called only under the map publication fence after the original map
commit. Stop inhibits new entries immediately; export requires proved worker
join. Capacity never evicts history and never faults the scan/IMU mapper.
"""
from dataclasses import dataclass
import base64
import hashlib
import json
import math
import threading
import secrets
import time

MAX_ROWS=4096
MAX_ORIGINAL_BYTES=16*1024*1024
MAX_EXPORT_BYTES=40*1024*1024
CHUNK_BYTES=64*1024
CONTEXT=('source_session','generation','source_clock_domain','receipt_clock_domain','map_epoch','map_frame')


def require(value,reason):
    if not value:raise ValueError(reason)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')


@dataclass(frozen=True)
class _Entry:
    metadata: bytes
    original_cdr: bytes
    seal: bytes


class CommittedPoseLedger:
    def __init__(self,*,enabled=False,capacity=MAX_ROWS,original_bytes=MAX_ORIGINAL_BYTES):
        require(type(enabled) is bool and type(capacity) is int and 1<=capacity<=MAX_ROWS and
                type(original_bytes) is int and 4096<=original_bytes<=MAX_ORIGINAL_BYTES,'typed_bounded_pose_ledger_required')
        self.enabled=enabled;self.capacity=capacity;self.byte_cap=original_bytes
        self.lock=threading.RLock();self.cancel=threading.Event()
        self.entries=[];self.original_bytes=0;self.reason=None;self.joined=False
        self._exports={};self._floors={};self.gnss=[];self.gnss_bytes=0;self.gnss_complete=True
        self._live_prefix=None

    def inhibit(self):self.cancel.set()

    def start_after_join(self,*,explicit=False):
        require(explicit is True,'explicit_pose_ledger_start_required')
        with self.lock:
            require(self.joined or (not self.entries and not self.cancel.is_set()),'previous_pose_ledger_join_required')
            self.joined=False;self._exports.clear();self._live_prefix=None;self.cancel.clear()

    def close_after_join(self):
        self.inhibit()
        with self.lock:self.joined=True

    def append_committed(self,frame,pose,version,chain,original_cdr,*,validate):
        if not self.enabled or self.cancel.is_set():return False
        require(callable(validate),'exact_committed_pose_validator_required')
        with self.lock:
            require(validate() is True,'original_map_commit_changed')
            ctx=frame.context
            require(type(ctx) is tuple and len(ctx)==6 and type(ctx[1]) is int and 0<=ctx[1]<2**63 and
                    all(type(v) is str and 1<=len(v)<=128 for k,v in enumerate(ctx) if k!=1),'exact_native_context_required')
            require(type(version) is int and 0<version<2**63 and type(chain) is str and 0<len(chain)<=128 and
                    type(pose) is tuple and len(pose)==3 and all(type(v) in (int,float) and math.isfinite(v) for v in pose),
                    'exact_committed_pose_required')
            require(type(original_cdr) is bytes and 4<=len(original_cdr)<=262144 and
                    hashlib.sha256(original_cdr).hexdigest()==frame.scan_cdr_sha256[1],
                    'original_committed_scan_SHA_required')
            source,receipt=frame.scan_stamps_ns[1],frame.scan_receipts_ns[1]
            require(type(source) is int and type(receipt) is int and 0<=source<2**63 and 0<=receipt<2**63,
                    'original_integer_scan_clocks_required')
            key=tuple(ctx)
            floor=self._floors.get(key)
            require(floor is None or (source>floor[0] and receipt>=floor[1] and version>floor[2]),'committed_pose_rewind')
            if len(self.entries)>=self.capacity or self.original_bytes+len(original_cdr)>self.byte_cap:
                self.reason='retained_pose_history_capacity';return False
            row=dict(context=dict(zip(CONTEXT,ctx)),chain=chain,version=version,source_ns=source,
                callback_RAW_ns=receipt,xy=list(pose[:2]),yaw_rad=pose[2],scan_sha256=frame.scan_cdr_sha256[1],
                original_frame=frame.laser_frame,scan_gid=frame.scan_gid,imu_gid=frame.imu_gid,
                original_IMU_source_ns=frame.imu_stamp_ns,original_IMU_callback_RAW_ns=frame.imu_receipt_ns,
                original_IMU_sha256=frame.imu_cdr_sha256,original_pose_not_reconstructed=True,
                runtime_authority=False,motor_command_sent=False,physical_accuracy_verified=False)
            raw=canonical(row)
            require(len(raw)<=2048,'pose_ledger_entry_bound')
            seal=hashlib.sha256(len(raw).to_bytes(8,'big')+raw+original_cdr).digest()
            require(validate() is True,'original_map_commit_changed')
            if self.cancel.is_set():return False
            self.entries.append(_Entry(raw,original_cdr,seal));self.original_bytes+=len(original_cdr)
            self._floors[key]=(source,receipt,version)
            return True

    def document(self,*,include_scans=False):
        require(type(include_scans) is bool,'typed_scan_export_required')
        with self.lock:
            require(self.joined and self.cancel.is_set(),'joined_map_worker_before_export_required')
            if include_scans in self._exports:return self._exports[include_scans]
            rows=[]
            for entry in self.entries:
                require(hashlib.sha256(len(entry.metadata).to_bytes(8,'big')+entry.metadata+entry.original_cdr).digest()==entry.seal,
                        'retained_committed_pose_changed')
                row=json.loads(entry.metadata)
                require(hashlib.sha256(entry.original_cdr).hexdigest()==row['scan_sha256'],'retained_original_scan_changed')
                if include_scans:row['original_scan_CDR_hex']=entry.original_cdr.hex()
                rows.append(row)
            gps=[]
            for raw_gps,sealed in self.gnss:
                require(hashlib.sha256(raw_gps).digest()==sealed,'retained_original_GPS_changed')
                gps.append(json.loads(raw_gps))
            raw=canonical(dict(schema='ubts.ROOT.reviewed.native_lidar_imu.pose_history.v2',rows=rows,
                original_GNSS_pairs=gps,complete_retained_quality_GNSS_history=self.gnss_complete,
                contains_original_scans=include_scans,complete_retained_history=self.reason is None,
                ledger_reason=self.reason,runtime_authority=False,motor_command_sent=False,
                physical_accuracy_verified=False,host_ROS_stamps_are_receiver_epoch=False))
            require(len(raw)<=MAX_EXPORT_BYTES,'pose_history_export_bound')
            self._exports[include_scans]=raw
            return raw

    def append_gnss(self,original,*,validate):
        from native_gnss_fusion import OriginalGNSS
        require(type(original) is OriginalGNSS and callable(validate),'exact_owned_original_GPS_required')
        if not self.enabled or self.cancel.is_set():return False
        with self.lock:
            require(validate() is True,'original_GPS_binding_changed')
            size=len(original.data_cdr)+len(original.fix_cdr)
            if len(self.gnss)>=256 or self.gnss_bytes+size>4*1024*1024:
                self.gnss_complete=False
                return False
            raw=canonical(dict(context=list(original.context),data_CDR_hex=original.data_cdr.hex(),
                fix_CDR_hex=original.fix_cdr.hex(),data_CDR_sha256=hashlib.sha256(original.data_cdr).hexdigest(),
                fix_CDR_sha256=hashlib.sha256(original.fix_cdr).hexdigest(),data_gid=original.data_gid,
                fix_gid=original.fix_gid,data_callback_RAW_ns=original.data_callback_RAW_ns,
                fix_callback_RAW_ns=original.fix_callback_RAW_ns,source_stamps_unchanged=True,
                runtime_authority=False,motor_command_sent=False))
            require(validate() is True,'original_GPS_binding_changed')
            if self.cancel.is_set():return False
            self.gnss.append((raw,hashlib.sha256(raw).digest()));self.gnss_bytes+=size
            return True

    def mark_gnss_capacity(self):
        with self.lock:self.gnss_complete=False

    def chunk(self,offset=0,*,include_scans=False,expected_sha256=None):
        require(type(offset) is int and 0<=offset<=MAX_EXPORT_BYTES,'bounded_export_offset_required')
        raw=self.document(include_scans=include_scans);sha=hashlib.sha256(raw).hexdigest()
        require((offset==0 and expected_sha256 is None) or expected_sha256==sha,'frozen_export_SHA_required')
        require(offset<=len(raw),'export_offset_out_of_range')
        part=raw[offset:offset+CHUNK_BYTES]
        return dict(ok=True,schema='ubts.native.committed_pose_export.chunk.v1',sha256=sha,total_bytes=len(raw),
            offset=offset,next_offset=None if offset+len(part)==len(raw) else offset+len(part),
            data_base64=base64.b64encode(part).decode('ascii'),runtime_authority=False,motor_command_sent=False)

    def open_live_prefix(self):
        """Pin one immutable prefix, independent of future map/GPS appends.

        Only reference copying is under the append lock. Index encoding, seal
        validation and chunk encoding run on the existing UDS request loop,
        outside native callbacks and the asynchronous map worker. No new thread,
        clock sampling, original timestamp renewal or cell mutation occurs.
        """
        require(self.enabled, 'native_pose_ledger_default_OFF')
        now=time.monotonic()  # Client resource lease only, never a source clock.
        with self.lock:
            require(self._live_prefix is None or now>=self._live_prefix[0],
                    'one_pinned_live_history_prefix_required')
            poses=tuple(self.entries);gps=tuple(self.gnss)
            reason=self.reason;gps_complete=self.gnss_complete
            token=secrets.token_hex(16)
            # Pending pin blocks concurrent opens while encoding outside lock.
            pin=(now+60.,token,poses,gps,None,None)
            self._live_prefix=pin
        try:
            index=canonical(dict(schema='ubts.native.live_pose_history.index.v1',
                snapshot_scope='fixed_prefix_at_open',current_run_complete=False,
                pose_entry_sha256=[entry.seal.hex() for entry in poses],
                GNSS_entry_sha256=[seal.hex() for _raw,seal in gps],
                complete_retained_history=reason is None,ledger_reason=reason,
                complete_retained_quality_GNSS_history=gps_complete,
                source_stamps_unchanged=True,host_ROS_stamps_are_receiver_epoch=False,
                GNSS_correction_applied_to_cells=False,fused_occupancy_qualified=False,
                runtime_authority=False,motor_command_sent=False,autonomy_output_enabled=False))
            require(len(index)<=1024*1024,'bounded_live_history_index_required')
            digest=hashlib.sha256(index).hexdigest()
            with self.lock:
                require(self._live_prefix is pin,'live_history_prefix_revoked')
                self._live_prefix=(*pin[:4],index,digest)
            return dict(ok=True,snapshot=token,index_sha256=digest,index_bytes=len(index),
                pose_rows=len(poses),quality_GNSS_pairs=len(gps),snapshot_scope='fixed_prefix_at_open',
                current_run_complete=False,source_stamps_unchanged=True,
                GNSS_correction_applied_to_cells=False,fused_occupancy_qualified=False,
                runtime_authority=False,motor_command_sent=False)
        except BaseException:
            with self.lock:
                if self._live_prefix is pin:self._live_prefix=None
            raise

    def live_prefix_chunk(self,snapshot,*,kind='index',entry=0,offset=0,expected_sha256=None):
        require(type(snapshot) is str and len(snapshot)==32 and
                type(kind) is str and kind in ('index','pose','GNSS') and
                type(entry) is int and entry>=0 and type(offset) is int and offset>=0,
                'typed_live_history_chunk_required')
        with self.lock:
            pin=self._live_prefix
            require(pin is not None and time.monotonic()<pin[0] and snapshot==pin[1] and pin[4] is not None,
                    'owned_unexpired_live_history_prefix_required')
        # Never hold the append lock across CDR digest or base64/JSON work.
        if kind=='index':
            require(entry==0,'live_history_index_entry_zero_required')
            raw,digest=pin[4],pin[5]
        elif kind=='pose':
            require(entry<len(pin[2]),'live_history_pose_index_out_of_range')
            original=pin[2][entry]
            raw=len(original.metadata).to_bytes(8,'big')+original.metadata+original.original_cdr
            digest=original.seal.hex()
            require(hashlib.sha256(raw).hexdigest()==digest,'retained_committed_pose_changed')
            metadata=json.loads(original.metadata)
            require(hashlib.sha256(original.original_cdr).hexdigest()==metadata['scan_sha256'],
                    'retained_original_scan_changed')
        else:
            require(entry<len(pin[3]),'live_history_GNSS_index_out_of_range')
            raw,sealed=pin[3][entry];digest=sealed.hex()
            require(hashlib.sha256(raw).hexdigest()==digest,'retained_original_GPS_changed')
        require(type(expected_sha256) is str and expected_sha256==digest and offset<=len(raw),
                'frozen_live_history_entry_SHA_required')
        part=raw[offset:offset+CHUNK_BYTES]
        with self.lock:
            require(self._live_prefix is pin and time.monotonic()<pin[0],
                    'live_history_prefix_revoked')
        return dict(ok=True,schema='ubts.native.live_pose_history.chunk.v1',snapshot=snapshot,
            kind=kind,entry=entry,sha256=digest,total_bytes=len(raw),offset=offset,
            next_offset=None if offset+len(part)==len(raw) else offset+len(part),
            data_base64=base64.b64encode(part).decode('ascii'),
            source_stamps_unchanged=True,runtime_authority=False,motor_command_sent=False)

    def release_live_prefix(self,snapshot):
        with self.lock:
            require(type(snapshot) is str and self._live_prefix is not None and
                    snapshot==self._live_prefix[1],'owned_live_history_prefix_required')
            self._live_prefix=None
        return dict(ok=True,released=True,runtime_authority=False,motor_command_sent=False)

    def status(self):
        with self.lock:return dict(enabled=self.enabled,rows=len(self.entries),original_bytes=self.original_bytes,
            max_rows=self.capacity,max_original_bytes=self.byte_cap,reason=self.reason,
            original_GNSS_pairs=len(self.gnss),original_GNSS_bytes=self.gnss_bytes,
            complete_retained_quality_GNSS_history=self.gnss_complete,
            stopped=self.cancel.is_set(),joined=self.joined,full_history_preserved=self.reason is None,
            retained_entries_never_evicted=True,
            source_stamps_unchanged=True,runtime_authority=False,motor_command_sent=False)
