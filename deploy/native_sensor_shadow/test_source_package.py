"""Portable installed-layout tests; no ROS, native child or device IO."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import physical_shadow_runner as runner
import verify_source as source


class PortableSourceTests(unittest.TestCase):
    def test_exact_portable19_and_adapter_verified(self):
        result=source.verify()
        self.assertTrue(result['ok']);self.assertEqual(result['build_files_verified'],19)
        self.assertFalse(result['runtime_authority'])

    def test_default_runner_has_no_ROS_or_native_launch(self):
        out=io.StringIO();before=set(sys.modules)
        with patch.object(sys,'argv',['physical_shadow_runner.py']),contextlib.redirect_stdout(out),patch.object(runner.subprocess,'Popen',side_effect=AssertionError('nochild')):
            self.assertEqual(runner.main(),0)
        self.assertFalse(json.loads(out.getvalue())['child_spawned'])
        self.assertFalse(any(x=='rclpy' or x.startswith('rclpy.') for x in set(sys.modules)-before))

    def test_source_tamper_and_adapter_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);m=json.loads((source.HERE/'SOURCE.json').read_bytes())
            for name in ['SOURCE.json','Dockerfile.adapter']+list(m['build_files_sha256']):
                target=p/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((source.HERE/name).read_bytes())
            f=p/'physical-package/physical_factory.py';f.write_bytes(f.read_bytes()+b'\n')
            with self.assertRaisesRegex(ValueError,'source_bytes_changed'):source.verify(p)
            f.write_bytes((source.HERE/'physical-package/physical_factory.py').read_bytes())
            f=p/'Dockerfile.adapter';f.write_bytes(f.read_bytes()+b'\n')
            with self.assertRaisesRegex(ValueError,'adapter_changed'):source.verify(p)

    def test_installed_minimal13_loads_without_upstream_private_files(self):
        old=list(sys.path)
        try:
            package=source.HERE/'physical-package'
            self.assertEqual(runner.load_package(package,'714fa332f73983fe4bffe1606831663365e6c121fa049a18873e2b53479f5077'),package.resolve())
            import inputs
            self.assertFalse(inputs.verify()['deep_SIM_or_external_BASE_modules_needed'])
            self.assertFalse((package/'test_physical.py').exists())
        finally:sys.path[:]=old


if __name__=='__main__':unittest.main()
