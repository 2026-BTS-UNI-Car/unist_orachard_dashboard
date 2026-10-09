"""Portable synthetic regressions for the opt-in physical surface cache.

Default package: ../deploy/native_sensor_stream/optimized-package from a repo
tests directory. An isolated review copy can use --package PATH. Each case runs
in a fresh -I Python process, so common/kernel imports never affect other tests.
No recorded CDR, ROS, clocks from devices, or private fixture imports are used.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import unittest


_PACKAGE = Path(__file__).resolve().parents[1] / 'deploy/native_sensor_stream/optimized-package'


def _child(case, package):
    sys.path.insert(0, str(package.resolve(strict=True)))
    import numpy as np
    from kernel import raw_localization as kernel

    check = unittest.TestCase()

    def dense(dtype=float, shift=0.):
        x = np.linspace(-2., 2., 800)
        return (np.vstack((np.column_stack((x, np.full_like(x, 1.))),
                           np.column_stack((np.full_like(x, 1.), x)))) + shift).astype(dtype)

    def equal(a, b):
        check.assertEqual(a.denominator, b.denominator)
        for name in ('points', 'normals', 'dense'):
            x, y = getattr(a, name), getattr(b, name)
            check.assertEqual(x.dtype, y.dtype)
            check.assertEqual(x.shape, y.shape)
            check.assertEqual(x.tobytes(), y.tobytes())
        for name in ('tree', 'dense_tree'):
            x, y = getattr(a, name), getattr(b, name)
            check.assertEqual(x.data.tobytes(), y.data.tobytes())
            for query in (a.points[::17], a.dense[::23]):
                for left, right in zip(x.query(query, k=3, workers=1),
                                       y.query(query, k=3, workers=1)):
                    check.assertEqual(left.tobytes(), right.tobytes())

    if case == 'default_off':
        data = dense()
        cache = kernel.ExactSurfaceCache()
        check.assertIs(cache.enabled, False)
        equal(kernel.surfaces(data), kernel.surfaces(data, surface_cache=cache))
        check.assertEqual(len(cache._entries), 0)
        for value in (0, 1, None, 'true'):
            with check.assertRaisesRegex(ValueError, 'typed_surface_cache_opt_in_required'):
                kernel.ExactSurfaceCache(enabled=value)
    elif case == 'dtype_fallback':
        for dtype in (np.dtype('float32'),
                      np.dtype(float).newbyteorder('>' if sys.byteorder == 'little' else '<')):
            data = dense(dtype)
            original = kernel.surfaces(data)
            cache = kernel.ExactSurfaceCache(enabled=True)
            for _ in range(2):
                equal(original, cache.resolve(data, 480))
                check.assertEqual(len(cache._entries), 0)
            public = kernel.ExactSurfaceCache(enabled=True)
            equal(original, kernel.surfaces(data, surface_cache=public))
            equal(original, kernel.surfaces(data, surface_cache=public))
            check.assertEqual(len(public._entries), 1)
            check.assertEqual(data.dtype, dtype)
    elif case == 'mutation':
        data = dense()
        untouched = data.tobytes()
        cache = kernel.ExactSurfaceCache(enabled=True)
        result = kernel.surfaces(data, surface_cache=cache)
        equal(kernel.surfaces(data), result)
        check.assertEqual(data.tobytes(), untouched)
        retained = result.points.tobytes()
        for array in (result.points, result.normals, result.dense,
                      result.tree.data, result.dense_tree.data):
            with check.assertRaises(ValueError):
                array.setflags(write=True)
        result.points = np.zeros((1, 2))
        again = kernel.surfaces(data, surface_cache=cache)
        check.assertEqual(again.points.tobytes(), retained)
        data += .125
        check.assertEqual(again.points.tobytes(), retained)
        equal(kernel.surfaces(data), kernel.surfaces(data, surface_cache=cache))
    elif case == 'capacity_and_results':
        cache = kernel.ExactSurfaceCache(enabled=True)
        first_key = None
        for index, (maximum, shift) in enumerate(((480, 0.), (240, 0.), (480, .1), (480, .2))):
            data = dense(shift=shift)
            equal(kernel.surfaces(data, maximum),
                  kernel.surfaces(data, maximum, surface_cache=cache))
            equal(kernel.surfaces(data, maximum),
                  kernel.surfaces(data, maximum, surface_cache=cache))
            check.assertLessEqual(len(cache._entries), 2)
            if index == 0:
                first_key = next(iter(cache._entries))
        check.assertEqual(len(cache._entries), 2)
        check.assertNotIn(first_key, cache._entries)
    elif case == 'digest_collision':
        import hashlib
        from unittest.mock import patch

        class Digest:
            def digest(self):
                return b'synthetic-collision'

        cache = kernel.ExactSurfaceCache(enabled=True)
        with patch.object(hashlib, 'sha256', lambda _: Digest()):
            kernel.surfaces(dense(), surface_cache=cache)
            changed = dense(shift=.2)
            equal(kernel.surfaces(changed), kernel.surfaces(changed, surface_cache=cache))
    elif case == 'factory_constructor':
        from common.native_ownership import RawReceipt
        from physical_ingress import PhysicalOriginalIngress
        from physical_factory import PhysicalShadowContract, PhysicalNativeShadowFactory, revision
        import inputs

        class SyntheticRaw:
            def sample(self):
                return RawReceipt(5_000_000_000, '01' * 32)

        contract = PhysicalShadowContract('synthetic_laser', 'synthetic_imu')
        receiver = PhysicalOriginalIngress(raw_clock=SyntheticRaw(),
            source_now_ns=lambda: 1_790_000_000_000_000_000,
            source_clock_kind=lambda: 'ROS_SYSTEM_TIME', scan_frame=contract.scan_frame,
            imu_frame=contract.imu_frame, profile_revision=revision(contract),
            scan_publisher_gid=b'\x01' * 16, imu_publisher_gid=b'\x02' * 16,
            observer_contract_sha256=inputs.OBSERVER_SHA)
        receiver.reset()
        unit = PhysicalNativeShadowFactory(receiver, contract)
        check.assertIs(unit.exact_surface_cache_enabled, False)
        check.assertIs(unit._exact_surface_cache.enabled, False)
        for invalid in (0, 1, None, 'true'):
            with check.assertRaisesRegex(ValueError, 'typed_constructor_surface_cache_option_required'):
                PhysicalNativeShadowFactory(receiver, contract, exact_surface_cache_enabled=invalid)
        with check.assertRaises(AttributeError):
            unit.exact_surface_cache_enabled = True
        a = PhysicalNativeShadowFactory(receiver, contract, exact_surface_cache_enabled=True)
        b = PhysicalNativeShadowFactory(receiver, contract, exact_surface_cache_enabled=True)
        check.assertIsNot(a._exact_surface_cache, b._exact_surface_cache)
        equal(kernel.surfaces(dense()),
              kernel.surfaces(dense(), surface_cache=a._exact_surface_cache))
        check.assertEqual(len(b._exact_surface_cache._entries), 0)
    else:
        raise ValueError('unknown_synthetic_test_case')
    print('PASS:' + case)


class PortableSurfaceCacheTests(unittest.TestCase):
    def isolated(self, case):
        env = dict(os.environ, UBTS_MAP_FIT_WORKERS='1', OPENBLAS_NUM_THREADS='1',
                   MKL_NUM_THREADS='1', OMP_NUM_THREADS='1')
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()),
            '--child', case, '--package', str(_PACKAGE)], env=env,
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS:' + case)

    def test_default_off_strict_bool(self):
        self.isolated('default_off')

    def test_float32_and_nonnative_dtype_fallback(self):
        self.isolated('dtype_fallback')

    def test_input_wrapper_and_array_mutation(self):
        self.isolated('mutation')

    def test_two_entry_cap_and_exact_results(self):
        self.isolated('capacity_and_results')

    def test_hash_collision_requires_exact_bytes(self):
        self.isolated('digest_collision')

    def test_factory_constructor_only_opt_in(self):
        self.isolated('factory_constructor')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--package', type=Path, default=_PACKAGE)
    parser.add_argument('--child')
    args, rest = parser.parse_known_args()
    _PACKAGE = args.package
    if args.child:
        _child(args.child, _PACKAGE)
    else:
        unittest.main(argv=[sys.argv[0], *rest])
