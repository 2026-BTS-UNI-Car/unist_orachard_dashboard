"""Encoder errors survive temporary archive cleanup, without unbounded logs."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_adaptive_recording import run,vfr_encoder_timing


class EncoderDiagnosticsTest(unittest.TestCase):
    def test_modern_duration_capability_does_not_combine_output_rate_with_vfr(self):
        help_text='setts AVOptions:\n  -pts <string> default PTS\n  -duration <string> default DURATION\n'
        with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,stdout=help_text)) as probe:
            args,info=vfr_encoder_timing('/opt/ffmpeg')
        self.assertNotIn('-r',args)
        self.assertIn('setts=duration=0.001/TB',args)
        self.assertEqual(args[args.index('-fps_mode')+1],'vfr')
        self.assertEqual(args[args.index('-enc_time_base')+1],'1:1000')
        self.assertIsNone(info['encoder_nominal_rate_hz'])
        probe.assert_called_once_with(['/opt/ffmpeg','-hide_banner','-h','bsf=setts'],
                                      capture_output=True,text=True,timeout=10)

    def test_legacy_without_duration_option_keeps_identical_nominal_clock(self):
        # A prose mention of duration is not evidence of the public option.
        help_text='setts AVOptions:\n  -pts <string> PTS\n packet duration is unavailable\n'
        with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,stdout=help_text)):
            args,info=vfr_encoder_timing()
        self.assertEqual(args,['-r','1000','-fps_mode','vfr','-enc_time_base','1:1000'])
        self.assertEqual(info['encoder_nominal_rate_hz'],1000)
        self.assertEqual(info['terminal_duration_mode'],'legacy_vfr_nominal_clock')

    def test_failed_capability_probe_does_not_guess_or_drop_timing_hint(self):
        with patch('subprocess.run',return_value=subprocess.CompletedProcess([],1,stdout='')):
            with self.assertRaisesRegex(RuntimeError,'timing capability probe failed'):
                vfr_encoder_timing()

    def test_failure_reports_current_encoder_diagnosis_and_preserves_cause(self):
        with tempfile.TemporaryDirectory() as folder:
            log=Path(folder)/'encoder.log'
            log.write_bytes(b'old successful encode\n')
            def fail(args,**kwargs):
                kwargs['stdout'].write(b'Failed to configure output pad on scale\n'
                                       b'Resource temporarily unavailable\n')
                raise subprocess.CalledProcessError(1,args)
            with patch('subprocess.run',side_effect=fail),patch('shutil.which',return_value=None):
                with self.assertRaises(RuntimeError) as captured:
                    run(['ffmpeg','synthetic-input'],log,cwd=Path(folder))
            message=str(captured.exception)
            self.assertIn('exit 1',message)
            self.assertIn('Failed to configure output pad',message)
            self.assertIn('Resource temporarily unavailable',message)
            self.assertNotIn('old successful encode',message)
            self.assertIsInstance(captured.exception.__cause__,subprocess.CalledProcessError)
            self.assertIn(b'old successful encode',log.read_bytes())

    def test_large_log_is_bounded_and_invalid_utf8_does_not_hide_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            log=Path(folder)/'encoder.log'
            def fail(args,**kwargs):
                kwargs['stdout'].write(b'previous current-command noise\n'+b'x'*16000+
                                       b'\xff\nactual final encoder failure\n')
                raise subprocess.CalledProcessError(7,args)
            with patch('subprocess.run',side_effect=fail),patch('shutil.which',return_value=None):
                with self.assertRaises(RuntimeError) as captured:run(['ffmpeg'],log)
            message=str(captured.exception)
            self.assertIn('exit 7',message)
            self.assertIn('actual final encoder failure',message)
            self.assertNotIn('previous current-command noise',message)
            self.assertLess(len(message),4200)

    def test_empty_diagnostic_still_reports_exit_code(self):
        with tempfile.TemporaryDirectory() as folder:
            log=Path(folder)/'encoder.log'
            with patch('subprocess.run',side_effect=subprocess.CalledProcessError(9,['ffmpeg'])),\
                 patch('shutil.which',return_value=None):
                with self.assertRaisesRegex(RuntimeError,'exit 9.*no encoder diagnostic output'):
                    run(['ffmpeg'],log)

    def test_lost_log_does_not_replace_original_encoder_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            log=Path(folder)/'encoder.log'
            def fail(args,**kwargs):
                log.unlink()
                raise subprocess.CalledProcessError(3,args)
            with patch('subprocess.run',side_effect=fail),patch('shutil.which',return_value=None):
                with self.assertRaisesRegex(RuntimeError,'exit 3.*encoder log unavailable'):
                    run(['ffmpeg'],log)

    def test_success_and_legacy_vfr_flag_are_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            log=Path(folder)/'encoder.log'
            calls=[]
            def successful(args,**kwargs):
                calls.append((args,kwargs))
                if kwargs.get('capture_output'):
                    return subprocess.CompletedProcess(args,0,stdout='legacy FFmpeg help')
                kwargs['stdout'].write(b'success\n')
                return subprocess.CompletedProcess(args,0)
            with patch('subprocess.run',side_effect=successful),patch('shutil.which',return_value='/usr/bin/nice'):
                self.assertIsNone(run(['ffmpeg','-fps_mode','vfr','native.mp4'],log,cwd=folder))
            self.assertEqual(calls[1][0],['nice','-n','10','ffmpeg','-vsync','vfr','native.mp4'])
            self.assertTrue(calls[1][1]['check'])
            self.assertEqual(calls[1][1]['cwd'],folder)
            self.assertEqual(log.read_bytes(),b'success\n')


if __name__=='__main__':unittest.main()
