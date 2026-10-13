"""Synthetic UBX/CDR/date/custody regressions; no driver/ROS/time IO."""
import calendar,copy,gzip,json,struct,threading,unittest
from pathlib import Path
from dashboard_server.gps import GnssDecoder,ubx_packet
from dashboard_server.gps_utc import receiver_utc,decorate_receiver_time,mark_publication
from tools.gnss_receiver_utc.utc_admission import ReceiverUTCAdmissions
from deploy.native_sensor_stream import native_gnss_fusion as native
HERE=Path(__file__).resolve().parent
CTX=('synthetic-session',1,'synthetic-source','CLOCK_MONOTONIC_RAW:test','source-manifest','profile')
def packet(*,second=28,nano=1,tow=1000,bits=7,tacc=17000):
    b=bytearray(92);struct.pack_into('<IHBBBBBB',b,0,tow,2026,10,10,6,56,second,bits);struct.pack_into('<Ii',b,12,tacc,nano)
    b[20]=3;b[21]=1;b[23]=10;struct.pack_into('<iiiiII',b,24,100000000,100000000,0,0,8000,12000)
    return ubx_packet(1,7,b)
def sample(**kw):return GnssDecoder(clock=lambda:100.).feed(packet(**kw))[0]
def original(data,sec=1700000000,ns=1,fix_ns=None):
    data=copy.deepcopy(data);data.setdefault('schema','orchard.gps.v1');data.setdefault('frame_id','gps_link');mark_publication(data,sec,ns)
    def pack(raw,fmt,alignment,*v):raw.extend(b'\0'*((alignment-(len(raw)-4)%alignment)%alignment));raw.extend(struct.pack('<'+fmt,*v))
    def string(raw,text):s=text.encode()+b'\0';pack(raw,'I',4,len(s));raw.extend(s)
    raw=bytearray(b'\0\1\0\0');string(raw,json.dumps(data,allow_nan=False,separators=(',',':')))
    fix=bytearray(b'\0\1\0\0');pack(fix,'iI',4,sec,ns if fix_ns is None else fix_ns);string(fix,'gps_link')
    pack(fix,'b',1,0 if data.get('valid') else -1);pack(fix,'H',2,0);pack(fix,'3d',8,10.,10.,0.);pack(fix,'9d',8,64.,0.,0.,0.,64.,0.,0.,0.,144.);pack(fix,'B',1,1)
    return native.OriginalGNSS(CTX,bytes(raw),bytes(fix))
class UTCTests(unittest.TestCase):
    def test_exact_input_frame_custody_checksum_and_raw_decoded_tamper_rejected(self):
        raw=packet();d=GnssDecoder(clock=lambda:100.).feed(raw)[0]
        self.assertEqual(bytes.fromhex(d['receiver_pvt_frame_hex']),raw);self.assertEqual(bytes.fromhex(d['receiver_pvt_payload_hex']),raw[6:-2])
        self.assertTrue(receiver_utc(d)['raw_checksum_independently_verified'])
        bad=bytearray(raw);bad[22]^=1;x=copy.deepcopy(d);x['receiver_pvt_frame_hex']=bytes(bad).hex()
        with self.assertRaisesRegex(ValueError,'checksum_invalid'):receiver_utc(x)
        payload=bytearray(raw[6:-2]);payload[11]=3;changed=ubx_packet(1,7,payload);x=copy.deepcopy(d)
        import hashlib
        x.update(receiver_pvt_frame_hex=changed.hex(),receiver_pvt_frame_sha256=hashlib.sha256(changed).hexdigest(),receiver_pvt_payload_hex=changed[6:-2].hex())
        with self.assertRaisesRegex(ValueError,'raw_PVT_decoded_time_mismatch'):receiver_utc(x)
        x=copy.deepcopy(d);x['receiver_pvt_frame_hex']='00'*101
        with self.assertRaisesRegex(ValueError,'bounded_original_PVT'):receiver_utc(x)
    def test_rawless_historical_and_direct_payload_argument_do_not_earn_frame_admission(self):
        d=sample()
        for key in ('receiver_pvt_frame_hex','receiver_pvt_frame_sha256','receiver_pvt_payload_hex'):d.pop(key,None)
        e=receiver_utc(d);self.assertEqual(e['raw_time_evidence_kind'],'decoded_fields_only_raw_unavailable');self.assertFalse(e['raw_checksum_independently_verified'])
        a=ReceiverUTCAdmissions(CTX,enabled=True,validate_original=lambda o:True);r=a.admit(original(d));self.assertFalse(r['accepted']);self.assertIn('verified_original_PVT_frame_required',r['reason'])
        p=packet()[6:-2];only=GnssDecoder(clock=lambda:100.)._ubx(1,7,p)
        self.assertEqual(only['receiver_pvt_payload_hex'],p.hex());self.assertNotIn('receiver_pvt_frame_hex',only)
        self.assertEqual(receiver_utc(only)['raw_time_evidence_kind'],'payload_argument_only_checksum_unverified')
        r=a.admit(original(only));self.assertFalse(r['accepted']);self.assertIn('verified_original_PVT_frame_required',r['reason']);self.assertEqual(a.records,[])
    def test_decoder_preserves_signed_nano_integer_accuracy_flags_calendar_and_old_seconds(self):
        for nano in (1,-123456789,999999999,-1000000000,1000000000):
            d=sample(nano=nano);self.assertEqual(d['gps_time'],'2026-10-10T06:56:28+00:00');self.assertEqual(d['gps_time_fraction_ns'],nano)
            self.assertEqual(d['gps_time_accuracy_ns'],17000);self.assertEqual(d['gps_time_valid_bits'],7);self.assertEqual(d['gps_time_calendar_utc']['second'],28)
            expected=calendar.timegm((2026,10,10,6,56,28))*10**9+nano
            self.assertEqual(receiver_utc(d)['receiver_utc_ns'],expected);self.assertEqual(d['receiver_time_evidence']['receiver_utc_ns'],str(expected))
            self.assertFalse(d['receiver_time_evidence']['scan_IMU_time_association_verified'])
        d=sample();hp=bytearray(36);struct.pack_into('<I',hp,4,1000);decoder=GnssDecoder(clock=lambda:100.);decoder.feed(packet());frame=decoder.feed(ubx_packet(1,20,hp))[0]
        self.assertEqual(frame['gps_time_origin_protocol'],'UBX_NAV_PVT');self.assertEqual(frame['receiver_time_evidence'],d['receiver_time_evidence'])
        with self.assertRaisesRegex(ValueError,'NAV_PVT'):receiver_utc(frame)
    def test_unresolved_leap_timezone_fraction_accuracy_and_date_reject_without_clamping(self):
        d=sample(second=60);self.assertEqual(d['gps_time_calendar_utc']['second'],60);self.assertIsNone(d['gps_time']);self.assertEqual(d['receiver_time_evidence']['reason'],'unsupported_UTC_leap_second')
        for changes in ({'gps_time_fully_resolved':False},{'gps_time':'2026-10-10T06:56:28'},{'gps_time':'2026-10-10T15:56:28+09:00'},
            {'gps_time':'2026-02-30T06:56:28Z'},{'gps_time':'2026-10-10T06:56:28.000000001Z'},
            {'gps_time_fraction_ns':True},{'gps_time_fraction_ns':1000000001},{'gps_time_accuracy_s':float('nan')},{'gps_time_accuracy_ns':17001},{'gps_time_valid_bits':3}):
            x=sample();x.update(changes);self.assertFalse(decorate_receiver_time(x)['accepted_reported_UTC'])
        invalid=sample(bits=3);self.assertTrue(invalid['valid']);self.assertFalse(invalid['receiver_time_evidence']['accepted_reported_UTC'])
    def test_default_OFF_exact_CDR_custody_pair_and_header_not_measurement(self):
        calls=[];off=ReceiverUTCAdmissions(CTX,validate_original=lambda o:calls.append(o));self.assertFalse(off.admit(original(sample()))['accepted']);self.assertFalse(calls)
        a=ReceiverUTCAdmissions(CTX,enabled=True,validate_original=lambda o:True);r=a.admit(original(sample()))
        self.assertTrue(r['accepted']);e=r['record'];self.assertNotEqual(e['receiver_utc_ns'],e['paired_header_field_ns']);self.assertIsNone(e['host_measurement_time_ns'])
        self.assertFalse(e['source_certificate_qualified']);self.assertFalse(e['scan_IMU_time_association_verified']);self.assertEqual(e['ordinary_GPS_uncertainty_floor_m'],8.)
        mismatch=original(sample(second=29,tow=2000),ns=2,fix_ns=3);self.assertFalse(a.admit(mismatch)['accepted']);self.assertEqual(len(a.records),1)
    def test_iTow_week_wrap_repeats_reordering_and_retained_capacity(self):
        a=ReceiverUTCAdmissions(CTX,enabled=True,validate_original=lambda o:True,capacity=2)
        first=sample(nano=-100000000,tow=604799900);second=sample(nano=100000000,tow=100)
        self.assertTrue(a.admit(original(first,ns=1))['accepted']);self.assertTrue(a.admit(original(second,ns=2))['accepted'])
        old=list(a.records);self.assertFalse(a.admit(original(sample(second=29,tow=1100),ns=3))['accepted']);self.assertEqual(a.records,old)
        b=ReceiverUTCAdmissions(CTX,enabled=True,validate_original=lambda o:True)
        self.assertTrue(b.admit(original(sample(),ns=1))['accepted']);self.assertFalse(b.admit(original(sample(),ns=2))['accepted'])
        wrong=sample(second=30,tow=2000);self.assertFalse(b.admit(original(wrong,ns=3))['accepted']);self.assertEqual(len(b.records),1)
    def test_Stop_during_external_custody_check_prevents_late_UTC_and_keeps_prefix(self):
        gate=threading.Event();entered=threading.Event();a=ReceiverUTCAdmissions(CTX,enabled=True,validate_original=lambda o:True);self.assertTrue(a.admit(original(sample()))['accepted'])
        def slow(o):entered.set();self.assertTrue(gate.wait(3));return True
        a.validate=slow;out={};t=threading.Thread(target=lambda:out.update(a.admit(original(sample(second=29,tow=2000),ns=2))));t.start();self.assertTrue(entered.wait(2));a.stop();gate.set();t.join(3)
        self.assertFalse(t.is_alive());self.assertFalse(out['accepted']);self.assertEqual(len(a.records),1)
    def test_legacy_decoded_JSON_cannot_gain_raw_packet_custody(self):
        d=sample();d.pop('receiver_pvt_frame_hex');d.pop('receiver_pvt_frame_sha256');d.pop('receiver_pvt_payload_hex')
        a=ReceiverUTCAdmissions(CTX,enabled=True,validate_original=lambda _:True)
        r=a.admit(original(d));self.assertFalse(r['accepted']);self.assertEqual(len(a.records),0)
    def test_original_GNSS_class_is_actual_public_namespace(self):
        self.assertEqual(native.OriginalGNSS.__module__,'deploy.native_sensor_stream.native_gnss_fusion')

    def test_reported_UTC_can_be_valid_without_a_position_fix_no_factor_or_fake_quality(self):
        d=sample();d.update(valid=False,h_acc_m=None,quality=0,latitude=None,longitude=None)
        a=ReceiverUTCAdmissions(CTX,enabled=True,validate_original=lambda o:True);r=a.admit(original(d));self.assertTrue(r['accepted'])
        self.assertFalse(r['record']['reported_position_valid']);self.assertIsNone(r['record']['ordinary_GPS_uncertainty_floor_m']);self.assertFalse(r['record']['position_constraint_applied'])

    def test_Stop_commit_wins_transition_is_locked_and_no_append_after_return(self):
        from .stop_seam_probe import commit_wins
        result=commit_wins(ReceiverUTCAdmissions,CTX,original(sample()),original(sample(second=29,tow=2000),ns=2))
        self.assertFalse(result['event_set_while_commit_lock_owned'])
        self.assertFalse(result['Stop_returned_before_release'])
        self.assertTrue(result['admit_accepted'])
        self.assertFalse(result['append_observed_stopped_event'])
        self.assertEqual(result['trace'],['append','Stop_return'])
        self.assertEqual(result['prefix_retained'],True)
        self.assertEqual(result['records_at_Stop_return'],2)
        self.assertEqual(result['records_after_Stop_return'],2)
        self.assertFalse(result['late_admission_accepted'])
        self.assertTrue(result['all_threads_joined'])
    def test_Stop_wins_queued_commit_keeps_prefix_and_rejects_late_record(self):
        from .stop_seam_probe import stop_wins
        result=stop_wins(ReceiverUTCAdmissions,CTX,original(sample()),original(sample(second=29,tow=2000),ns=2))
        self.assertTrue(result['Stop_returned_before_queued_admission'])
        self.assertFalse(result['queued_admission_accepted'])
        self.assertEqual(result['reason'],'stopped')
        self.assertTrue(result['prefix_retained'])
        self.assertEqual(result['records_at_Stop_return'],1)
        self.assertEqual(result['records_after_Stop_return'],1)
        self.assertTrue(result['all_threads_joined'])
