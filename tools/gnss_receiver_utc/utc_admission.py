"""DEFAULT-OFF bounded UTC admission, not a GPS factor or sensor clock bridge."""
from collections import Counter
import hashlib,math,threading
from deploy.native_sensor_stream import native_gnss_fusion as native
from dashboard_server.gps_utc import receiver_utc,UTC_ROLE

class ReceiverUTCAdmissions:
    def __init__(self,context,*,enabled=False,validate_original=None,capacity=64):
        native.require(type(enabled) is bool and type(capacity) is int and 1<=capacity<=256,'typed_bounded_UTC_admission')
        self.context=native.context(context);self.enabled=enabled;self.validate=validate_original;self.capacity=capacity
        self.stop_event=threading.Event();self.lock=threading.RLock();self.records=[];self.rejected=Counter()
    def stop(self):
        # Linearization: event transition and admission append share one lock.
        # Parsing/custody callbacks remain outside this bounded commit section.
        with self.lock:
            self.stop_event.set()
            return self.status()
    def status(self):
        with self.lock:return dict(enabled=self.enabled,stopped=self.stop_event.is_set(),records=len(self.records),rejected=dict(self.rejected),
            receiver_UTC_only=True,scan_IMU_time_association_verified=False,source_certificate_qualified=False,physical_outputs=False)
    def admit(self,original):
        if not self.enabled or self.stop_event.is_set():return dict(accepted=False,reason='default_OFF_or_stopped')
        try:
            native.require(type(original) is native.OriginalGNSS and original.context==self.context and callable(self.validate) and self.validate(original) is True,'external_original_pair_custody_required')
            data,header=native.decode_gps_data(original.data_cdr);fix=native.decode_navsat(original.fix_cdr)
            native.require(header==fix['stamp_ns'] and data.get('frame_id')==fix['frame_id']=='gps_link','exact_original_GPS_pair_required')
            native.require(data.get('stamp_role') in (None,UTC_ROLE),'receiver_time_is_not_header_time')
            e=receiver_utc(data);h=data.get('h_acc_m')
            native.require(e['raw_checksum_independently_verified'] is True,'verified_original_PVT_frame_required_not_legacy_JSON_or_payload')
            valid_h=type(h) in (int,float) and math.isfinite(h) and 0<h<=50
            position_valid=data.get('valid') is True and fix['status']>=0
            scale=max(5.,float(h)) if valid_h and position_valid else None
            record=dict(e,context=self.context,data_cdr_sha256=hashlib.sha256(original.data_cdr).hexdigest(),fix_cdr_sha256=hashlib.sha256(original.fix_cdr).hexdigest(),
                paired_header_field_ns=header,header_role=UTC_ROLE if data.get('stamp_role')==UTC_ROLE else 'undeclared_legacy_header_role',
                ordinary_GPS_uncertainty_floor_m=scale,reported_h_acc_m=float(h) if valid_h else None,reported_position_valid=position_valid,
                position_constraint_applied=False,GPS_yaw_applied=False)
            with self.lock:
                if self.stop_event.is_set():return dict(accepted=False,reason='stopped')
                native.require(len(self.records)<self.capacity,'retained_UTC_capacity')
                if self.records:
                    prior=self.records[-1];delta=e['receiver_utc_ns']-prior['receiver_utc_ns'];tow=(e['receiver_i_tow_ms']-prior['receiver_i_tow_ms'])%604800000
                    native.require(delta>0 and 0<tow<302400000,'receiver_epoch_repeated_or_reordered')
                    native.require(abs(delta-tow*10**6)<=5_000_000+e['time_accuracy_ns']+prior['time_accuracy_ns'],'receiver_UTC_iTow_rate_mismatch_or_leap')
                    native.require(header>prior['paired_header_field_ns'],'header_field_duplicate_or_reordered')
                self.records.append(record);return dict(accepted=True,record=record)
        except (ValueError,TypeError,KeyError,OverflowError,RecursionError) as error:
            reason=str(error)[:128] or type(error).__name__
            with self.lock:
                key=reason if reason in self.rejected or len(self.rejected)<31 else 'other_rejection'
                self.rejected[key]+=1
            return dict(accepted=False,reason=reason)
