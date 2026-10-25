"""TurnGuard: shapes, phase binding, shape-change rule, duty ladder rule, budgets (V-T1)."""
import unittest

import rowexec_testlib  # noqa: F401  (path setup)
from row_executor.guard import FORWARD, PIVOT, OutputGuard, TurnGuard


def guard(direction=1, **kw):
    args = dict(pivot_min_pct=60, pivot_cap_pct=80, drive_min_pct=20, drive_cap_pct=65, drive_diff_cap_pct=10)
    args.update(kw)
    return TurnGuard(direction, **args)


class Shapes(unittest.TestCase):
    def test_zero_always_allowed(self):
        g = guard()
        for phase in (None, PIVOT, FORWARD, 'stop'):
            self.assertIsNone(g.check(0., 0, 0, phase))

    def test_left_pivot_shape_only(self):
        g = guard(1)
        self.assertIsNone(g.check(0., -60, 60, PIVOT))
        self.assertEqual(g.check(0., 60, -60, PIVOT), 'pivot_wrong_direction')
        self.assertEqual(g.check(0., -60, 70, PIVOT), 'pivot_unequal')
        self.assertEqual(g.check(0., -50, 50, PIVOT), 'pivot_duty_out_of_range')
        self.assertEqual(g.check(0., -90, 90, PIVOT), 'pivot_duty_out_of_range')

    def test_right_pivot_mirrored(self):
        g = guard(-1)
        self.assertIsNone(g.check(0., 70, -70, PIVOT))
        self.assertEqual(g.check(0., -70, 70, PIVOT), 'pivot_wrong_direction')

    def test_never_allowed_shapes(self):
        g = guard()
        self.assertEqual(g.check(0., -40, -40, FORWARD), 'reverse_command')
        self.assertEqual(g.check(0., 0, 40, FORWARD), 'one_side_only')
        self.assertEqual(g.check(0., -60, 0, PIVOT), 'reverse_command')
        self.assertEqual(g.check(0., True, 40, FORWARD), 'non_integer_duty')
        self.assertEqual(g.check(0., 40., 40, FORWARD), 'non_integer_duty')

    def test_forward_limits(self):
        g = guard()
        self.assertIsNone(g.check(0., 50, 60, FORWARD))
        self.assertEqual(g.check(0., 60, 70, FORWARD), 'drive_above_cap')
        self.assertEqual(g.check(0., 15, 20, FORWARD), 'drive_below_breakaway')
        self.assertEqual(g.check(0., 40, 52, FORWARD), 'drive_differential_above_cap')

    def test_phase_binding(self):
        g = guard()
        self.assertEqual(g.check(0., -60, 60, FORWARD), 'shape_not_allowed_in_phase')
        self.assertEqual(g.check(0., 50, 50, PIVOT), 'shape_not_allowed_in_phase')
        self.assertEqual(g.check(0., 50, 50, None), 'output_in_stop_phase')

    def test_output_guard_unchanged(self):
        """The row executor's OutputGuard still refuses every pivot (the turn guard is a separate class)."""
        og = OutputGuard(75, 40)
        self.assertEqual(og.check(-60, 60), 'pivot_command')

    def test_constructor_validation(self):
        for bad in (dict(pivot_min_pct=90, pivot_cap_pct=80), dict(drive_min_pct=70, drive_cap_pct=65),
                    dict(pivot_cap_pct=101), dict(step_interval_s=-1.)):
            with self.assertRaises(ValueError):
                guard(**bad)
        with self.assertRaises(ValueError):
            TurnGuard(0, 60, 80, 20, 65, 10)


class Transitions(unittest.TestCase):
    def test_shape_change_needs_zero_time_and_stillness(self):
        g = guard()
        t = 10.
        self.assertEqual(g.apply(t, 55, 55, FORWARD, still_s=0.)[2], None)
        self.assertEqual(g.apply(t + .05, -60, 60, PIVOT, still_s=1.)[2], 'shape_change_without_stop')
        g = guard()
        g.apply(t, 55, 55, FORWARD)
        g.apply(t + .05, 0, 0, None)           # zero since t+0.05; need 0.55 + 0.32 = 0.87 s
        self.assertEqual(g.check(t + .5, -60, 60, PIVOT, still_s=1.), 'shape_change_zero_too_short')
        self.assertEqual(g.check(t + 1., -60, 60, PIVOT, still_s=.2), 'shape_change_not_still')
        self.assertIsNone(g.check(t + 1., -60, 60, PIVOT, still_s=.5))

    def test_violation_returns_zero_and_counts(self):
        g = guard()
        self.assertEqual(g.apply(0., 60, -60, PIVOT), (0, 0, 'pivot_wrong_direction'))
        self.assertEqual(g.violations, 1)
        self.assertEqual(g.last_out, (0, 0))

    def test_pivot_ladder_rule(self):
        g = guard()
        g.apply(0., -60, 60, PIVOT, yaw_rate_deg_s=0.)
        self.assertEqual(g.check(.5, -70, 70, PIVOT), 'pivot_duty_step_too_soon')
        self.assertEqual(g.check(1., -80, 80, PIVOT), 'pivot_duty_step_too_large')
        self.assertIsNone(g.check(1., -70, 70, PIVOT))
        g.apply(1., -70, 70, PIVOT)
        self.assertEqual(g.check(1.5, -60, 60, PIVOT), 'pivot_duty_decrease')
        g.apply(1.5, 0, 0, None)
        self.assertIsNone(g.check(2., -60, 60, PIVOT))     # after a zero any duty in range may start

    def test_stall_loaded_budget_per_step(self):
        g = guard(stall_loaded_total_s=1.)
        t = 0.
        g.apply(t, -60, 60, PIVOT, yaw_rate_deg_s=0.)
        for _ in range(19):
            t += .05
            self.assertIsNone(g.apply(t, -60, 60, PIVOT, yaw_rate_deg_s=0.)[2])
        t += .05
        self.assertEqual(g.apply(t + .05, -60, 60, PIVOT, yaw_rate_deg_s=0.)[2], 'stall_loaded_budget')
        g.reset_step()
        g2_t = t + 1.
        self.assertIsNone(g.apply(g2_t, -60, 60, PIVOT, yaw_rate_deg_s=0.)[2])

    def test_rotation_does_not_count_as_stall_loaded(self):
        g = guard(stall_loaded_total_s=1.)
        t = 0.
        for _ in range(60):
            self.assertIsNone(g.apply(t, -60, 60, PIVOT, yaw_rate_deg_s=15.)[2])
            t += .05
        self.assertLess(g.stall_loaded_s, .1)
        self.assertGreater(g.pivot_time_s, 2.5)

    def test_pivot_time_budget(self):
        g = guard(pivot_total_s=2.)
        t = 0.
        result = None
        for _ in range(60):
            result = g.apply(t, -60, 60, PIVOT, yaw_rate_deg_s=15.)[2]
            if result:
                break
            t += .05
        self.assertEqual(result, 'pivot_time_budget')


if __name__ == '__main__':
    unittest.main()
