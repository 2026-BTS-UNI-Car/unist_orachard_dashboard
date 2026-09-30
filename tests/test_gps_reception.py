"""Synthetic UBX and Linux PTY only; no receiver, ROS or physical hardware."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dashboard_server.gps import GnssDecoder, fresh_snapshot, ubx_packet
from dashboard_server.gps_reception import reception_snapshot, WEEK_MS
from scripts.gps_ws_bridge import GpsBridgeState


def sat(rows=(), tow=1000, version=1):
    payload = bytearray(struct.pack('<IBBxx', tow, version, len(rows)))
    for gnss, sv, cno, quality in rows:
        payload.extend(struct.pack('<BBBbhhI', gnss, sv, cno, 0, 0, 0, quality))
    return ubx_packet(1, 0x35, payload)


def sig(rows=(), tow=1000, version=0):
    payload = bytearray(struct.pack('<IBBxx', tow, version, len(rows)))
    for gnss, sv, signal, cno, quality in rows:
        payload.extend(struct.pack('<BBBBhBBBBH4x', gnss, sv, signal, 0, 0, cno, quality, 0, 0, 0))
    return ubx_packet(1, 0x43, payload)


def nofix(tow=1000):
    payload = bytearray(92); struct.pack_into('<I', payload, 0, tow)
    return ubx_packet(1, 7, payload)


class ReceptionDecoderTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.decoder = GnssDecoder(clock=lambda: self.now)

    def snapshot(self, connected=True):
        return reception_snapshot(self.decoder.reception, now=self.now, connected=connected)

    def test_signal_detection_tracking_and_used_satellites_are_independent(self):
        # Multiple frequencies of one satellite remain separate signals.
        self.decoder.feed(sig([(0, 8, 0, 30, 7), (0, 8, 3, 28, 4), (6, 18, 0, 27, 3),
                               (3, 2, 0, 0, 1), (0, 27, 0, 20, 2)]))
        self.decoder.feed(sat([(0, 8, 30, 7), (6, 18, 27, 3), (3, 2, 0, 1)]))
        frame = self.decoder.feed(nofix())[0]
        self.assertFalse(frame['valid']); self.assertEqual(frame['satellites'], 0)
        self.assertEqual(frame['reception']['nav_sig']['reported_count'], 5)
        self.assertEqual(frame['reception']['nav_sig']['detected_count'], 4)
        self.assertEqual(frame['reception']['nav_sig']['tracked_count'], 2)
        self.assertEqual(frame['reception']['nav_sat']['detected_count'], 2)
        self.assertEqual(frame['reception']['nav_sat']['tracked_count'], 1)
        self.assertIsNone(frame['latitude'])

    def test_diagnostics_without_navigation_never_create_or_refresh_fix(self):
        self.assertEqual(self.decoder.feed(sig([(0, 8, 0, 30, 7)])), [])
        self.assertEqual(self.decoder.sample, {}); self.assertIsNone(self.decoder.last_sample_at)
        frame = self.decoder.feed(nofix())[0]
        previous = self.decoder.last_sample_at
        self.now += 10
        self.decoder.feed(sig([(0, 8, 0, 30, 7)], tow=11000))
        self.assertEqual(self.decoder.last_sample_at, previous)
        snap = fresh_snapshot(frame, previous, now=self.now, reception=self.decoder.reception)
        self.assertEqual(snap['state'], 'stale')
        self.assertEqual(snap['reception']['nav_sig']['state'], 'fresh')

    def test_empty_report_is_zero_but_missing_is_unknown(self):
        self.assertIsNone(self.snapshot()['nav_sig']['detected_count'])
        self.decoder.feed(sig())
        self.assertEqual(self.snapshot()['nav_sig']['detected_count'], 0)
        self.assertEqual(self.snapshot()['nav_sig']['state'], 'fresh')
        self.assertEqual(self.snapshot()['nav_sat']['state'], 'no_data')

    def test_quality_values_are_not_cno_threshold_or_fix_inference(self):
        self.decoder.feed(sig([(0, q+1, 0, 0 if q >= 4 else 20, q) for q in range(8)]))
        r = self.snapshot()['nav_sig']
        self.assertEqual((r['detected_count'], r['tracked_count'], r['positive_cno_count']), (6, 4, 4))

    def test_fragmented_mixed_packets_preserve_sat_sig_separate_epochs(self):
        stream = sat([(0, 8, 30, 7)], tow=1000)+sig([(0, 8, 0, 29, 4)], tow=1200)+nofix(1300)
        frames=[]
        for i in range(0,len(stream),3): frames += self.decoder.feed(stream[i:i+3])
        self.assertEqual(len(frames),1); self.assertEqual(self.decoder.rejected,0)
        self.assertEqual(self.snapshot()['nav_sat']['i_tow_ms'],1000)
        self.assertEqual(self.snapshot()['nav_sig']['i_tow_ms'],1200)

    def test_bad_version_length_quality_duplicate_and_tow_are_rejected_without_crash(self):
        good = sig([(0, 8, 0, 30, 7)])
        bad = [sig(version=1), sat(version=0), sig([(0, 8, 0, 30, 8)]),
               sig([(0, 8, 0, 30, 7)]*2), sat([(0, 8, 30, 7)]*2),
               sig(tow=WEEK_MS), ubx_packet(1,0x43,good[6:-3]),
               ubx_packet(1,0x35,b'\0'*7), sig([(0, i, 0, 0, 0) for i in range(121)])]
        for packet in bad:
            with self.subTest(packet=packet[:12]):
                decoder=GnssDecoder(clock=lambda:self.now)
                self.assertEqual(decoder.feed(packet),[])
                self.assertEqual(decoder.rejected,1)
                self.assertEqual(len(decoder.feed(nofix())),1)
                self.assertIn('invalid', [v['state'] for k,v in reception_snapshot(decoder.reception,now=self.now).items() if isinstance(v,dict)])

    def test_checksum_failure_does_not_create_report_and_next_packet_recovers(self):
        packet=sig([(0,8,0,30,7)])
        self.decoder.feed(packet[:-1]+bytes([packet[-1]^1]))
        self.assertEqual(self.snapshot()['nav_sig']['state'],'no_data')
        self.decoder.feed(packet)
        self.assertEqual(self.snapshot()['nav_sig']['tracked_count'],1)

    def test_duplicate_epoch_does_not_renew_and_regression_is_invalid(self):
        packet=sig([(0,8,0,30,7)])
        self.decoder.feed(packet); self.now+=12
        self.decoder.feed(packet)
        self.assertEqual(self.snapshot()['nav_sig']['age_s'],12)
        self.assertEqual(self.snapshot()['nav_sig']['state'],'fresh')
        self.now+=.001; self.decoder.feed(packet)
        self.assertEqual(self.snapshot()['nav_sig']['state'],'stale')
        self.decoder.feed(sig(tow=999))
        self.assertEqual(self.snapshot()['nav_sig']['state'],'invalid')
        self.decoder.feed(sig(tow=1001))
        self.assertEqual(self.snapshot()['nav_sig']['state'],'fresh')

    def test_week_wrap_advances_and_original_timestamp_is_retained(self):
        self.decoder.feed(sig(tow=WEEK_MS-1000)); self.now+=2
        self.decoder.feed(sig(tow=1000))
        self.assertEqual(self.snapshot()['nav_sig']['received_at'],102.)
        self.assertEqual(self.snapshot(False)['nav_sig']['state'],'disconnected')
        self.assertIsNone(self.snapshot(False)['nav_sig']['tracked_count'])

    def test_bridge_heartbeat_and_browser_snapshots_do_not_renew_diagnostics(self):
        self.decoder.feed(sig([(0,8,0,30,7)]))
        raw=fresh_snapshot({},None,now=self.now,reception=self.decoder.reception,driver={'serial_open':True})
        state=GpsBridgeState()
        with patch('scripts.gps_ws_bridge.time.time',return_value=110.):
            state.update(SimpleNamespace(data=json.dumps(raw)),True)
            snap=state.snapshot()
        self.assertEqual(snap['state'],'no_data')
        self.assertEqual(snap['reception']['nav_sig']['age_s'],10.)
        with patch('scripts.gps_ws_bridge.time.time',return_value=113.):
            state.update(SimpleNamespace(data=json.dumps(raw)),True)
            old=state.snapshot()
        self.assertEqual(old['reception']['nav_sig']['state'],'stale')
        self.assertIsNone(old['reception']['nav_sig']['tracked_count'])
        self.assertEqual(fresh_snapshot(old,None,now=114.)['reception']['nav_sig']['state'],'stale')
        missing=fresh_snapshot({},None,now=110.)
        self.assertEqual(fresh_snapshot(missing,None,now=111.)['reception']['nav_sig']['state'],'no_data')

    def test_driver_disconnect_revokes_recent_fix_and_diagnostic_status_loss_is_explicit(self):
        self.decoder.feed(sig([(0,8,0,30,7)]))
        frame=dict(schema='orchard.gps.v1',valid=True,latitude=1.,longitude=2.,received_at=100.)
        state=GpsBridgeState()
        with patch('scripts.gps_ws_bridge.time.time',return_value=100.):
            state.update(SimpleNamespace(data=json.dumps(frame)))
            status=fresh_snapshot(frame,100.,now=100.,driver={'serial_open':True},reception=self.decoder.reception)
            state.update(SimpleNamespace(data=json.dumps(status)),True)
            self.assertTrue(state.snapshot()['valid'])
            status['driver']['serial_open']=False
            state.update(SimpleNamespace(data=json.dumps(status)),True)
            self.assertFalse(state.snapshot()['valid'])
            self.assertEqual(state.snapshot()['reception']['nav_sig']['state'],'disconnected')
            status['driver']['serial_open']=True
            state.update(SimpleNamespace(data=json.dumps(status)),True)
        with patch('scripts.gps_ws_bridge.time.time',return_value=104.):
            self.assertEqual(state.snapshot()['reception']['nav_sig']['state'],'disconnected')

    def test_clock_regression_and_disconnect_hide_counts_without_changing_fix(self):
        self.decoder.feed(sig([(0,8,0,30,7)]))
        self.now=99.
        self.assertEqual(self.snapshot()['nav_sig']['state'],'invalid')
        self.now=100.
        self.assertEqual(self.snapshot(False)['nav_sig']['state'],'disconnected')


@unittest.skipUnless(sys.platform=='linux','Linux PTY serial owner')
class ReceptionPollTests(unittest.TestCase):
    def test_bounded_read_only_five_second_poll_and_passive_mode(self):
        import pty
        from scripts import gps_ros2_driver as driver
        master,slave=pty.openpty(); os.set_blocking(master,False)
        with tempfile.TemporaryDirectory() as tmp:
            serial=driver.GpsSerial(os.ttyname(slave),38400,str(Path(tmp)/'lock'))
            try:
                serial.open(); serial.decoder.identity={'software':'fixture'}
                serial.decoder.last_sample_at=100.
                for now,diagnostic in ((100.,True),(101.,False),(104.999,False),(105.,True)):
                    with patch.object(driver.time,'monotonic',return_value=now),patch.object(driver.time,'time',return_value=100.):
                        serial.next_poll=0;serial.read()
                    expected=ubx_packet(1,7)+ubx_packet(1,20)
                    if diagnostic:expected+=ubx_packet(1,0x35)+ubx_packet(1,0x43)
                    self.assertEqual(os.read(master,4096),expected)
                serial.poll_ubx=False; serial.next_poll=0; serial.next_reception_poll=0
                serial.read()
                with self.assertRaises(BlockingIOError):os.read(master,4096)
                serial.decoder.feed(sig()); serial.close()
                self.assertEqual(serial.snapshot()['reception']['nav_sig']['state'],'disconnected')
                serial.open();self.assertEqual(serial.decoder.reception,{})
            finally:serial.close();os.close(master);os.close(slave)

    def test_partial_poll_write_preserves_single_batch_without_interleaving(self):
        import pty
        from scripts import gps_ros2_driver as driver
        master,slave=pty.openpty()
        with tempfile.TemporaryDirectory() as tmp:
            serial=driver.GpsSerial(os.ttyname(slave),38400,str(Path(tmp)/'lock'))
            try:
                serial.open(); serial.decoder.identity={'software':'fixture'}; serial.decoder.last_sample_at=100.
                expected=ubx_packet(1,7)+ubx_packet(1,20)+ubx_packet(1,0x35)+ubx_packet(1,0x43)
                chunks=[]
                def short(fd,data):chunks.append(data[:3]);return min(3,len(data))
                with patch.object(driver.os,'write',side_effect=short),patch.object(driver.time,'time',return_value=100.),patch.object(driver.time,'monotonic',return_value=100.):
                    for _ in range(11):serial.read()
                self.assertEqual(b''.join(chunks),expected)
                self.assertEqual(serial.pending_poll,b'')
            finally:serial.close();os.close(master);os.close(slave)


if __name__=='__main__':unittest.main()
