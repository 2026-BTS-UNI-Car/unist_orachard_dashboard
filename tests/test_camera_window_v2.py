"""Fake-clock protocol/credit tests; no sockets, camera, ROS or GPU."""
import ast
import math
from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs,urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.camera_stream_policy import CameraAckWindow,CameraStreamBudget,CameraWindowStreamBudget


class CameraWindowTests(unittest.TestCase):
    def setUp(self):
        self.now=0.;self.w=CameraAckWindow(3,'a'*32,clock=lambda:self.now)
        self.w.observe_source(('compressed','camera',1920,1080),1,1.)
    def send(self,count=1,size=300000):
        self.w.observe_source(self.w.source,count,float(count));return self.w.register(count,size)
    def ack(self,seq,count=None,**extra):
        row=dict(type='frame_ack',ack_protocol=2,stream_session='a'*32,source_epoch=1,
            delivery_sequence=seq,frame_count=seq if count is None else count,decode_status='decoded')
        row.update(extra);return self.w.acknowledge(row)
    def test_three_distinct_credits_out_of_order_and_duplicates(self):
        for i in(1,2,3):self.send(i)
        self.assertFalse(self.w.can_send());self.assertEqual(self.w.bytes,900000)
        self.now=.2;self.assertIsNotNone(self.ack(3));self.assertEqual(len(self.w.pending),2)
        self.assertIsNone(self.ack(3));self.assertEqual(len(self.w.pending),2)
        self.assertIsNotNone(self.ack(1));self.assertIsNotNone(self.ack(2))
        self.assertEqual(self.w.counts['accepted_out_of_order'],2)
        self.assertEqual(self.w.bytes,0);self.assertEqual(self.w.peak_frames,3)
    def test_future_wrong_session_source_frame_and_malformed_never_free(self):
        self.send();self.now=.1
        for args in((2,{}),(1,{'stream_session':'b'*32}),(1,{'source_epoch':2}),(1,{'frame_count':2}),
                     (1,{'ack_protocol':True}),(1,{'delivery_sequence':True}),(1,{'decode_status':'arm'})):
            self.assertIsNone(self.ack(args[0],**args[1]))
            self.assertEqual(len(self.w.pending),1);self.assertEqual(self.w.bytes,300000)
        self.assertEqual(self.w.counts['future'],1);self.assertEqual(self.w.counts['wrong_session'],1)
        self.assertEqual(self.w.counts['wrong_source'],1);self.assertEqual(self.w.counts['wrong_frame'],1)
    def test_deadline_absolute_noise_and_newer_ack_do_not_extend_oldest(self):
        self.send(1);self.now=1.;self.send(2);self.now=2.;self.send(3)
        self.assertEqual(self.w.deadline(),8.)
        self.now=7.9;self.assertIsNotNone(self.ack(3));self.assertIsNone(self.ack(99))
        self.assertEqual(self.w.deadline(),8.);self.now=8.
        self.assertTrue(self.w.expired());self.assertIsNone(self.ack(1));self.assertIn(1,self.w.pending)
    def test_packet_and_aggregate_byte_caps(self):
        maximum=self.w.MAX_PACKET_BYTES;self.send(1,maximum)
        self.assertFalse(self.w.can_send(maximum));self.assertFalse(self.w.can_send(maximum+1))
        with self.assertRaises(ValueError):self.w.register(1,maximum)
        self.assertEqual(self.w.bytes,maximum)
        self.now=.1;self.ack(1);self.assertTrue(self.w.can_send(maximum))
    def test_source_change_and_counter_clock_regression_close_without_new_credits(self):
        self.send(2)
        for key,count,stamp in((('raw','camera',1920,1080),3,3.),(self.w.source,1,1.),(self.w.source,3,1.),(self.w.source,2,2.1)):
            with self.assertRaisesRegex(ValueError,'camera_source_changed'):self.w.observe_source(key,count,stamp)
            self.assertEqual(len(self.w.pending),1)
        self.w.close();self.assertEqual(self.w.bytes,0);self.assertEqual(self.w.pending,{})
        self.assertFalse(self.w.can_send());self.assertIsNone(self.ack(1,2))
    def test_superseded_releases_exact_credit_without_claiming_decode(self):
        self.send();self.now=.1;r=self.ack(1,decode_status='superseded')
        self.assertEqual(r['decode_status'],'superseded');self.assertEqual(self.w.bytes,0)
    def test_invalid_types_nan_and_sequence_overflow_rejected(self):
        for limit in(True,1,4):
            with self.assertRaises(ValueError):CameraAckWindow(limit,'a'*32)
        self.send();self.now=math.nan;self.assertIsNone(self.ack(1));self.assertEqual(self.w.bytes,300000)
        self.now=.1;self.w.last_sequence=2**53-1
        with self.assertRaises(ValueError):self.w.register(1,1)
    def test_legacy_budget_is_unchanged_and_v2_is_service_bound_not_bandwidth(self):
        v1=CameraStreamBudget('detail');v2=CameraWindowStreamBudget('detail',ack_window=3)
        for b in(v1,v2):b.acknowledge(.2,300000,decode_status='decoded')
        self.assertEqual(v1.values[:4],(1280,720,4.25,70));self.assertEqual(v1.values[4],1275000)
        self.assertAlmostEqual(v2.values[2],12.75);self.assertEqual(v2.values[4],3825000)
        self.assertFalse(v2.status()['service_estimate_is_link_bandwidth'])
        self.assertEqual(v2.ceiling,v1.ceiling)
    def test_physical_byte_ceiling_and_preview_dimensions_unchanged(self):
        b=CameraWindowStreamBudget('detail',ack_window=3)
        b.acknowledge(.01,300000,decode_status='decoded')
        self.assertEqual(b.values,(1280,720,30.,70,4000000.))
        self.assertEqual(b.ceiling,4000000)
        self.assertEqual(CameraWindowStreamBudget('auto',ack_window=2).values[:2],(640,360))
    def test_explicit_tiny_slow_quality_and_rate_caps_unchanged(self):
        for p in('tiny','slow'):
            a=CameraStreamBudget(p);b=CameraWindowStreamBudget(p,ack_window=2)
            a.acknowledge(.2,300000);b.acknowledge(.2,300000)
            self.assertEqual(a.values,b.values)


class NegotiationTests(unittest.TestCase):
    def test_only_explicit_binary_v2_valid_window_can_opt_in(self):
        p=Path(__file__).resolve().parents[1]/'scripts/camera_ws_bridge.py'
        node=next(n for n in ast.parse(p.read_bytes()).body if isinstance(n,ast.FunctionDef)and n.name=='client_settings')
        ns=dict(parse_qs=parse_qs,urlparse=urlparse,Any=object);exec(compile(ast.Module(body=[node],type_ignores=[]),str(p),'exec'),ns)
        settings=ns['client_settings'];base='/ws/camera?transport=binary-v1&'
        self.assertEqual(settings(base+'flow=decode-ack')['ack_window'],1)
        for n in(2,3):self.assertEqual(settings(base+f'flow=decode-ack-v2&ack_window={n}')['ack_window'],n)
        for q in('flow=decode-ack-v2','flow=decode-ack-v2&ack_window=4','flow=decode-ack-v2&ack_window=2&ack_window=3','flow=decode-ack&ack_window=3'):
            self.assertEqual(settings(base+q)['ack_window'],1)
        self.assertEqual(settings('/ws/camera?flow=decode-ack-v2&ack_window=3')['ack_window'],1)

if __name__=='__main__':unittest.main()
