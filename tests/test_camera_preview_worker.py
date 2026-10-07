"""Blocking codecs/publications must not become a camera acquisition backlog."""
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import cv2

from scripts.camera_cadence import CameraCadence
from scripts.camera_preview_worker import LatestCameraWorker
from scripts.native_camera_encoder import NativeCameraEncoder
from tests.test_camera_cadence import DRIVER, publication_node


class LatestCameraWorkerTests(unittest.TestCase):
    def test_slow_preview_does_not_block_actual_driver_capture_or_native_submission(self):
        entered = threading.Event(); release = threading.Event()
        processed=[]
        def slow_process(frame, stamp, raw_due, generation):
            processed.append((stamp, raw_due));entered.set();release.wait(3)
        node=publication_node();node.preview_worker.close()
        node.preview_worker=LatestCameraWorker(slow_process)
        self.addCleanup(node.preview_worker.close)
        clock=SimpleNamespace(now=10.)
        node.get_clock=lambda:SimpleNamespace(now=lambda:SimpleNamespace(to_msg=lambda:clock.now))
        node.args.rotate_180=False
        node.capture_cadence=CameraCadence(30)
        node.apply_settings_file=lambda:False
        node.write_status=Mock();node.fail_count=0
        def read():
            clock.now+=1/30
            return True,np.zeros((4,8,3),np.uint8)
        node.cap=SimpleNamespace(read=read)
        with patch.object(DRIVER.time,'monotonic',side_effect=lambda:clock.now):
            node.tick()
        self.assertTrue(entered.wait(1))
        try:
            began=time.monotonic()
            with patch.object(DRIVER.time,'monotonic',side_effect=lambda:clock.now):
                for _ in range(60):node.tick()
            elapsed=time.monotonic()-began
            self.assertLess(elapsed,.5)
            self.assertEqual(node.frame_count,61)
            self.assertEqual(node.native_encoder.submit.call_count,61)
            self.assertEqual(node.preview_worker.status()['pending'],1)
            self.assertEqual(node.preview_worker.status()['overwritten'],59)
            newest_stamp=node.native_encoder.submit.call_args.args[1][0]
        finally:release.set()
        self.assertTrue(node.preview_worker.wait_idle())
        self.assertEqual(len(processed),2)
        self.assertEqual(processed[-1][0],newest_stamp)
        self.assertTrue(processed[-1][1]) # Raw_due is carried to the newest real frame.

    def test_reopen_cancels_inflight_encode_and_pending_previous_generation(self):
        entered=threading.Event();release=threading.Event();published=[]
        worker=None
        def process(frame,stamp,raw_due,generation):
            if stamp=='old':entered.set();release.wait(2)
            worker.publish_current(generation,lambda:published.append(stamp))
        worker=LatestCameraWorker(process);self.addCleanup(worker.close)
        old=worker.submit(object(),'old',False,time.monotonic())
        self.assertTrue(entered.wait(1))
        worker.submit(object(),'pending-old',True,time.monotonic())
        self.assertTrue(worker.reset())
        worker.submit(object(),'new',False,time.monotonic())
        release.set();self.assertTrue(worker.wait_idle())
        self.assertEqual(published,['new'])
        self.assertEqual(worker.status()['cancelled'],1)
        self.assertFalse(worker.publish_current(old,lambda:published.append('late-native'),'native'))

    def test_close_is_bounded_and_reports_an_uncancellable_blocking_codec(self):
        entered=threading.Event();release=threading.Event();published=[]
        worker=None
        def process(frame,stamp,raw_due,generation):
            entered.set();release.wait(2)
            worker.publish_current(generation,lambda:published.append(stamp))
        worker=LatestCameraWorker(process)
        worker.submit(object(),'old',False,time.monotonic());self.assertTrue(entered.wait(1))
        before=time.monotonic()
        self.assertFalse(worker.close(timeout=.03))
        self.assertLess(time.monotonic()-before,.3)
        self.assertEqual(worker.status()['error'],'camera_preview_worker_shutdown_timeout')
        self.assertEqual(worker.status()['pending'],0)
        self.assertIsNone(worker.submit(object(),'after-close',True,time.monotonic()))
        release.set();worker.thread.join(1)
        self.assertFalse(worker.thread.is_alive());self.assertEqual(published,[])

    def test_reset_barrier_timeout_is_explicit_without_blocking_capture_submission(self):
        entered=threading.Event();release=threading.Event()
        worker=LatestCameraWorker(lambda *args:None);self.addCleanup(worker.close)
        generation=worker.generation
        def callback():entered.set();release.wait(2)
        publishing=threading.Thread(target=lambda:worker.publish_current(generation,callback,'raw'))
        publishing.start();self.assertTrue(entered.wait(1))
        try:
            before=time.monotonic();self.assertFalse(worker.reset(timeout=.03))
            self.assertLess(time.monotonic()-before,.3)
            before=time.monotonic();worker.submit(object(),'fresh',False,time.monotonic())
            self.assertLess(time.monotonic()-before,.1)
            self.assertEqual(worker.status()['error'],'camera_preview_reset_publish_barrier_timeout')
        finally:release.set();publishing.join(1)

    def test_native_encoder_cancelled_generation_is_not_counted_as_published(self):
        encoder=NativeCameraEncoder(lambda *args:False,90)
        encoder.submit(np.zeros((4,8,3),np.uint8),('stamp',0))
        encoder.queue.join();encoder.close()
        self.assertEqual(encoder.status()['published'],0)
        self.assertEqual(encoder.status()['cancelled'],1)
        self.assertEqual(encoder.queue.maxsize,4)
        self.assertEqual(encoder.quality,90)

    def test_native_encoder_accepts_readonly_original_1080_without_resizing(self):
        frames=[]
        frame=np.zeros((1080,1920,3),np.uint8)
        frame[100:300,200:600]=[15,130,240]
        original=frame.copy();frame.flags.writeable=False
        def published(data,stamp,width,height):
            frames.append((data,stamp,width,height));return True
        encoder=NativeCameraEncoder(published,90)
        encoder.submit(frame,('native_stamp',7));encoder.queue.join();encoder.close()
        self.assertEqual(len(frames),1)
        data,stamp,width,height=frames[0]
        self.assertEqual((width,height),(1920,1080))
        self.assertEqual(stamp,('native_stamp',7))
        self.assertEqual(cv2.imdecode(np.frombuffer(data,np.uint8),1).shape,(1080,1920,3))
        np.testing.assert_array_equal(frame,original)
        self.assertEqual(encoder.status()['quality'],90)
        self.assertEqual(encoder.status()['published'],1)
        self.assertIsNone(encoder.status()['error'])


if __name__=='__main__':unittest.main()
