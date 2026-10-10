from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import gzip
import hashlib
import io
import json
import os
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
import inspect_interrupted_recording as m


def inventory(folder):
    return {p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob('*') if p.is_file() and not p.is_symlink()}


def fixture(folder,state='archived'):
    (folder/'metadata.json').write_text(json.dumps({'state':state,'id':'synthetic','original_stamp':42}))
    (folder/'gps.jsonl').write_bytes(b'{"stamp":1}\n{"stamp":2}\n')
    (folder/'motor.jsonl').write_bytes(b'{"requested":0,"applied":0}\n')
    (folder/'mapping_source').mkdir()
    (folder/'mapping_source/sensors.jsonl.gz').write_bytes(gzip.compress(b'{"scan_stamp":123}\n{"imu_stamp":124}\n'))
    bag=folder/'rosbag2';bag.mkdir();(bag/'metadata.yaml').write_bytes(b'rosbag2_bagfile_information: synthetic\n')
    (bag/'synthetic_0.mcap').write_bytes(b'not a validated bag')
    return folder


class InspectorTests(unittest.TestCase):
    def test_default_cli_inert_even_with_path_no_file_open(self):
        output=io.StringIO()
        with patch.object(m,'ReadOnlyTree',side_effect=AssertionError('must not inspect')),redirect_stdout(output):
            self.assertEqual(m.main(['--recording-dir','not-used']),0)
        self.assertFalse(json.loads(output.getvalue())['inspection_performed'])

    def test_normal_finalized_outputs_keep_state_and_never_claim_bag_readproof(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=fixture(Path(temp));before=inventory(folder);report=m.inspect_recording(folder)
            self.assertEqual(report['metadata']['state'],'archived')
            self.assertTrue(report['metadata']['valid_json'])
            for row in report['sidecars'].values():self.assertTrue(row['inspection_complete'])
            self.assertTrue(report['sidecars']['mapping_source/sensors.jsonl.gz']['gzip_footer_valid'])
            self.assertTrue(report['bag_components']['metadata_yaml_present'])
            self.assertEqual(len(report['bag_components']['components']),2)
            self.assertIsNone(report['recovery_needed'])
            self.assertFalse(report['rosbag_readproof']);self.assertFalse(report['recording_integrity_verified'])
            self.assertEqual(inventory(folder),before)

    def test_interrupted_jsonl_and_fsynced_gzip_prefix_are_readonly_not_finalized(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=fixture(Path(temp),'recording')
            (folder/'gps.jsonl').write_bytes(b'{"stamp":1}\n{"stamp":')
            output=io.BytesIO();stream=gzip.GzipFile(fileobj=output,mode='wb');stream.write(b'{"sequence":1}\n{"sequence":2}\n');stream.flush()
            interrupted=output.getvalue();stream.close()
            (folder/'mapping_source/sensors.jsonl.gz').write_bytes(interrupted)
            before=inventory(folder);report=m.inspect_recording(folder)
            gps=report['sidecars']['gps.jsonl'];gz=report['sidecars']['mapping_source/sensors.jsonl.gz']
            self.assertEqual(gps['complete_json_rows'],1);self.assertGreater(gps['unterminated_tail_bytes'],0)
            self.assertEqual(gz['complete_json_rows'],2);self.assertFalse(gz['gzip_footer_valid'])
            self.assertEqual(gz['error'],'gzip_footer_missing_or_incomplete')
            self.assertTrue(report['recovery_needed']);self.assertEqual(report['metadata']['state'],'recording')
            self.assertEqual(inventory(folder),before)

    def test_concatenated_gzip_members_and_last_unterminated_json_distinguished(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=fixture(Path(temp));(folder/'mapping_source/sensors.jsonl.gz').write_bytes(gzip.compress(b'{"a":1}\n')+gzip.compress(b'{"b":2}'))
            report=m.inspect_recording(folder);gz=report['sidecars']['mapping_source/sensors.jsonl.gz']
            self.assertEqual(gz['gzip_members_completed'],2);self.assertTrue(gz['gzip_footer_valid'])
            self.assertEqual(gz['complete_json_rows'],1);self.assertTrue(gz['final_unterminated_json_valid'])
            self.assertTrue(report['recovery_needed'])

    def test_checksum_corruption_and_trailing_garbage_never_report_valid_footer(self):
        for corrupt in ('checksum','trailing'):
            with self.subTest(corrupt=corrupt),tempfile.TemporaryDirectory() as temp:
                folder=fixture(Path(temp));path=folder/'mapping_source/sensors.jsonl.gz';raw=bytearray(path.read_bytes())
                if corrupt=='checksum':raw[-8]^=1
                else:raw.extend(b'not a gzip member')
                path.write_bytes(raw);before=inventory(folder);row=m.inspect_recording(folder)['sidecars']['mapping_source/sensors.jsonl.gz']
                self.assertFalse(row['gzip_footer_valid']);self.assertFalse(row['inspection_complete'])
                self.assertEqual(row['error'],'gzip_stream_or_checksum_invalid');self.assertEqual(inventory(folder),before)

    def test_input_cap_is_unknown_prefix_not_unterminated_artifact_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=fixture(Path(temp));row=m.inspect_recording(folder,limits=m.Limits(input_bytes_per_sidecar=5))['sidecars']['gps.jsonl']
            self.assertEqual(row['input_bytes_read'],5);self.assertEqual(row['read_limit'],'input_byte_limit')
            self.assertFalse(row['entire_file_bytes_read']);self.assertFalse(row['tail_is_artifact_end'])
            self.assertIsNone(row['final_unterminated_json_valid'])

    def test_decompression_bomb_capped_without_whole_output_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=fixture(Path(temp));(folder/'mapping_source/sensors.jsonl.gz').write_bytes(gzip.compress(b'{"x":1}\n'*100000))
            report=m.inspect_recording(folder,limits=m.Limits(decoded_bytes_per_sidecar=1000,line_bytes=100))
            row=report['sidecars']['mapping_source/sensors.jsonl.gz']
            self.assertEqual(row['decoded_bytes_read'],1000);self.assertEqual(row['read_limit'],'decoded_byte_limit')
            self.assertFalse(row['inspection_complete']);self.assertTrue(report['bounded_inspection_limited'])
            self.assertLessEqual(row['unterminated_tail_bytes'],100)

    def test_line_overflow_discards_only_counts_then_recovers_next_complete_line(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=fixture(Path(temp));(folder/'gps.jsonl').write_bytes(b'x'*100+b'\n{"x":1}\n')
            row=m.inspect_recording(folder,limits=m.Limits(line_bytes=16))['sidecars']['gps.jsonl']
            self.assertEqual(row['oversize_rows'],1);self.assertEqual(row['complete_json_rows'],1)
            self.assertLessEqual(row['unterminated_tail_bytes'],16)

    def test_metadata_limit_duplicate_nonfinite_utf8_and_missing_are_explicit(self):
        for data,error in ((b'{"state":"archived","state":"recording"}',True),(b'{"x":NaN}',True),(b'\xff',True),(b'{}',False)):
            with self.subTest(data=data),tempfile.TemporaryDirectory() as temp:
                folder=fixture(Path(temp));(folder/'metadata.json').write_bytes(data);row=m.inspect_recording(folder)['metadata']
                self.assertEqual(bool(row.get('error')),error)
        with tempfile.TemporaryDirectory() as temp:
            folder=fixture(Path(temp));report=m.inspect_recording(folder,limits=m.Limits(metadata_bytes=4))
            self.assertEqual(report['metadata']['read_limit'],'metadata_byte_limit')
            self.assertFalse(report['metadata']['valid_json']);self.assertTrue(report['bounded_inspection_limited'])
            (folder/'metadata.json').unlink();self.assertTrue(m.inspect_recording(folder)['recovery_needed'])

    def test_bag_inventory_bounded_and_never_reads_components(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=fixture(Path(temp));ino={(folder/'rosbag2'/name).stat().st_ino for name in ('metadata.yaml','synthetic_0.mcap')}
            real_read=os.read
            def read(fd,count):
                self.assertNotIn(os.fstat(fd).st_ino,ino)
                return real_read(fd,count)
            with patch.object(m.os,'read',read):report=m.inspect_recording(folder,limits=m.Limits(bag_entries=1))
            bag=report['bag_components'];self.assertEqual(len(bag['components']),1)
            self.assertEqual(bag['read_limit'],'entry_limit');self.assertFalse(bag['bag_content_read'])
            self.assertFalse(bag['rosbag_readproof'])

    def test_read_snapshot_change_is_not_claimed_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=fixture(Path(temp));target=(folder/'gps.jsonl').stat().st_ino;real_stat=os.fstat;calls={}
            def sampled(fd):
                value=real_stat(fd)
                if value.st_ino==target:
                    calls[fd]=calls.get(fd,0)+1
                    if calls[fd]>1:
                        return SimpleNamespace(st_dev=value.st_dev,st_ino=value.st_ino,st_mode=value.st_mode,
                            st_size=value.st_size,st_mtime_ns=value.st_mtime_ns+1,st_ctime_ns=value.st_ctime_ns,
                            st_file_attributes=getattr(value,'st_file_attributes',0))
                return value
            with patch.object(m.os,'fstat',sampled):row=m.inspect_recording(folder)['sidecars']['gps.jsonl']
            self.assertFalse(row['snapshot_stable']);self.assertFalse(row['inspection_complete'])

    def test_symlink_file_and_parent_and_bag_not_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);folder=base/'record';folder.mkdir();fixture(folder)
            outside=base/'outside';outside.mkdir();(outside/'secret').write_bytes(b'never read this')
            links=[]
            try:
                (folder/'gps.jsonl').unlink();os.symlink(outside/'secret',folder/'gps.jsonl');links.append(folder/'gps.jsonl')
                report=m.inspect_recording(folder)
                self.assertFalse(report['sidecars']['gps.jsonl']['inspection_complete'])
                parent=base/'linked_record';os.symlink(folder,parent,target_is_directory=True);links.append(parent)
                with self.assertRaises((m.InspectionRefused,OSError)):m.inspect_recording(parent)
                # Refused bag entries are lstat'ed, never opened or descended.
                os.symlink(outside,folder/'rosbag2'/'outside',target_is_directory=True);links.append(folder/'rosbag2'/'outside')
                report=m.inspect_recording(folder)
                self.assertIn('refused_link',{row['kind'] for row in report['bag_components']['components']})
            finally:
                for link in reversed(links):link.unlink()
            self.assertEqual((outside/'secret').read_bytes(),b'never read this')

    def test_path_traversal_and_wrong_types_refused(self):
        for name in ('../secret','/absolute','x/../secret','x\\secret','x:stream',''):
            with self.subTest(name=name),self.assertRaises(m.InspectionRefused):m._relative(name)
        for values in ({'line_bytes':True},{'seconds':float('nan')},{'bag_entries':257},{'seconds':11}):
            with self.subTest(values=values),self.assertRaises(ValueError):m.Limits(**values)
        with self.assertRaises(m.InspectionRefused):m.ReadOnlyTree('x/../other')

    def test_cooperative_time_budget_stops_without_false_eof(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=fixture(Path(temp))
            with patch.object(m.time,'monotonic',side_effect=[0]+[100]*100):report=m.inspect_recording(folder)
            self.assertTrue(report['bounded_inspection_limited'])
            self.assertFalse(report['sidecars']['gps.jsonl']['inspection_complete'])
            self.assertFalse(report['rosbag_readproof'])

    def test_large_unicode_inventory_report_capped_and_all_handles_released(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=fixture(Path(temp))
            for n in range(80):(folder/'rosbag2'/(str(n)+'한'*70+'.mcap')).write_bytes(b'x')
            report=m.inspect_recording(folder)
            self.assertLess(len(json.dumps(report,ensure_ascii=True).encode()),64*1024)
            self.assertFalse(report['bag_components']['inventory_complete'])
            self.assertEqual(report['bag_components']['read_limit'],'report_byte_limit')
            # On Windows a leaked no-delete handle would prevent this rename.
            renamed=folder.parent/(folder.name+'_renamed');folder.rename(renamed);renamed.rename(folder)


if __name__=='__main__':unittest.main()
