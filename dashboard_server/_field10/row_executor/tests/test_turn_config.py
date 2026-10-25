"""Turn config keys: defaults keep the feature disabled, ranges, feasibility, profiles, digest, on-site updates."""
import json
import unittest
from dataclasses import replace

import rowexec_testlib  # noqa: F401  (path setup)
import turn_testlib as T
from row_executor import config as C
from row_executor.config import ConfigError, ExecutorConfig
from row_executor.turn_assist import TurnAssist, TurnRequest, TurnRequestError, turn_onsite_update

REQ = TurnRequest(direction='left', row_width_m=3.5, x_c_m=1.5, reach_m=3.2, headland_depth_m=5.)


def _valid(cfg):
    """The row part of cfg made valid (row feasibility needs C2/C3 with motor output); turn part unchanged."""
    if cfg.motor_output_enabled and (cfg.breakaway_duty_pct is None or cfg.v_measured_m_s is None):
        cfg = replace(cfg, motor_output_enabled=False)
    return cfg.validate()


class Defaults(unittest.TestCase):
    def test_default_disabled_and_valid(self):
        cfg = ExecutorConfig().validate()
        self.assertFalse(cfg.turn_assist_enabled)
        self.assertIsNone(cfg.pivot_duty_start_pct)
        self.assertIsNone(cfg.pivot_duty_cap_pct)
        self.assertFalse(cfg.pivot_surface_ok)
        feas = C.turn_feasibility(cfg)
        self.assertFalse(feas['output_allowed'])
        self.assertEqual(feas['status'], 'unknown')

    def test_untouched_turn_block_keeps_dump_and_digest(self):
        cfg = ExecutorConfig()
        data = cfg.to_dict()
        self.assertFalse(set(C.TURN_KEYS) & set(data))
        self.assertEqual(len(C.TURN_KEYS), len(set(C.TURN_KEYS)))
        self.assertEqual(C.TURN_KEYS[0], 'turn_assist_enabled')
        # the digest is the one of the dump without any turn key (existing configs keep their digest)
        import hashlib
        text = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)
        self.assertEqual(cfg.digest(), hashlib.sha256(text.encode()).hexdigest())
        again = C.loads(C.dumps(cfg))
        self.assertEqual(again.digest(), cfg.digest())

    def test_touched_turn_block_round_trip(self):
        cfg = T.turn_config()
        data = cfg.to_dict()
        self.assertTrue(set(C.TURN_KEYS) <= set(data))
        again = C.loads(C.dumps(cfg))
        self.assertEqual(again, cfg)
        self.assertNotEqual(cfg.digest(), ExecutorConfig().digest())

    def test_runner_unaffected_by_turn_keys(self):
        """A row run still gets the forward-only OutputGuard (no pivot) with the turn feature enabled."""
        from row_executor.core import Runner
        runner = Runner(T.turn_config())
        self.assertEqual(runner.guard.check(-60, 60), 'pivot_command')


class Ranges(unittest.TestCase):
    def bad(self, **kw):
        with self.assertRaises(ConfigError) as ctx:
            replace(ExecutorConfig(), **kw).validate()
        return ctx.exception.problems

    def test_type_and_range(self):
        self.bad(pivot_duty_start_pct=20)
        self.bad(pivot_duty_cap_pct=101)
        self.bad(pivot_duty_start_pct=60.)
        self.bad(turn_assist_enabled=1)
        self.bad(yaw_disagree_abort_deg=40.)
        self.bad(turn_imu_gap_abort_s=float('nan'))
        self.bad(pivot_topup_max=3)

    def test_rules(self):
        self.assertTrue(any('above pivot_duty_cap_pct' in p for p in self.bad(pivot_duty_start_pct=80,
                                                                               pivot_duty_cap_pct=70)))
        self.assertTrue(any('pivot_high_duty_ack' in p for p in self.bad(pivot_duty_start_pct=60,
                                                                          pivot_duty_cap_pct=100)))
        replace(ExecutorConfig(), pivot_duty_start_pct=60, pivot_duty_cap_pct=100,
                pivot_high_duty_ack=C.PIVOT_HIGH_DUTY_ACK).validate()
        self.bad(pivot_high_duty_ack='yes')
        self.bad(yaw_disagree_warn_deg=10., yaw_disagree_abort_deg=8.)
        self.bad(pivot_coast_s_lo=.5, pivot_coast_s_hi=.3)
        self.bad(pivot_icr_ahead_left_m=.1)
        self.bad(turn_clear_min_during_m=1.5, turn_clear_radius_m=1.2)
        self.bad(turn_drive_duty_pct=0)
        self.bad(pivot_accept_error_deg=10., pivot_overshoot_max_deg=5.)

    def test_unknown_key_refused(self):
        with self.assertRaises(ConfigError):
            C.from_dict(dict(turn_assist_enabled=False, turn_autostart=True))


class Feasibility(unittest.TestCase):
    def test_enabled_with_output_needs_measurements(self):
        for kw, word in ((dict(pivot_surface_ok=False), 'pivot_surface_ok'),
                         (dict(pivot_duty_start_pct=None), 'C6'),
                         (dict(breakaway_duty_pct=None), 'C2'), (dict(v_measured_m_s=None), 'C3')):
            cfg = replace(T.turn_config(), **kw)       # turn ON + motor output ON, one measurement missing
            feas = C.turn_feasibility(cfg)
            self.assertFalse(feas['output_allowed'], kw)
            self.assertTrue(any(word in p for p in feas['problems']), (kw, feas))
            with self.assertRaises(TurnRequestError):
                TurnAssist(_valid(cfg)).start(0., REQ, motor_output=True)
        feas = C.turn_feasibility(replace(T.turn_config(), breakaway_duty_pct=50, base_duty_pct=75,
                                          reduce_duty_pct=75, max_differential_pct=10, duty_cap_pct=85))
        self.assertTrue(any('inner wheel' in p for p in feas['problems']), feas)   # turn drive 55-5 < 60
        feas = C.turn_feasibility(replace(T.turn_config(), turn_drive_duty_pct=70))   # 70 + 10 > duty_cap 75
        self.assertTrue(any('duty_cap_pct' in p for p in feas['problems']), feas)
        self.assertFalse(feas['output_allowed'])

    def test_turn_rules_never_invalidate_a_row_config(self):
        """Untouched turn keys: an existing row config (low duty cap) stays valid (defaults identical)."""
        cfg = replace(ExecutorConfig(), base_duty_pct=56, reduce_duty_pct=56, max_differential_pct=6,
                      duty_cap_pct=62, diff_cap_pct=12).validate()
        self.assertFalse(C.turn_feasibility(cfg)['output_allowed'])

    def test_enabled_without_motor_output_is_dry_only(self):
        cfg = replace(ExecutorConfig(), turn_assist_enabled=True).validate()
        self.assertFalse(C.turn_feasibility(cfg)['output_allowed'])
        ta = TurnAssist(cfg)
        with self.assertRaises(TurnRequestError):
            ta.start(0., REQ, motor_output=True)
        ta.start(0., REQ, motor_output=False)

    def test_feasible(self):
        cfg = T.turn_config()
        feas = C.turn_feasibility(cfg)
        self.assertTrue(feas['output_allowed'], feas)
        self.assertEqual(feas['status'], 'ok')

    def test_default_config_refuses_turn_output(self):
        ta = TurnAssist(ExecutorConfig())
        with self.assertRaises(TurnRequestError):
            ta.start(0., REQ, motor_output=True)

    def test_bench_profile_cannot_enable_turn(self):
        with self.assertRaises(ConfigError):
            replace(ExecutorConfig(), profile='bench', bench_ack=C.BENCH_ACK, turn_assist_enabled=True).validate()

    def test_field_profile_still_refuses_bench_pattern_with_turn(self):
        with self.assertRaises(ConfigError):
            replace(T.turn_config(), bench_pattern=((1., 20, 20),)).validate()
        with self.assertRaises(ConfigError):
            replace(T.turn_config(), bench_ack=C.BENCH_ACK).validate()


class Onsite(unittest.TestCase):
    def test_bounded_update(self):
        file_cfg = T.turn_config()
        new = turn_onsite_update(file_cfg, file_cfg, dict(pivot_duty_start_pct=65, pivot_surface_ok=False))
        self.assertEqual(new.pivot_duty_start_pct, 65)
        self.assertFalse(C.turn_feasibility(new)['output_allowed'])     # surface failed C6: no turn output
        again = turn_onsite_update(file_cfg, new, dict(pivot_surface_ok=True, pivot_icr_ahead_left_m=.05,
                                                        pivot_icr_left_left_m=.2))
        self.assertTrue(C.turn_feasibility(again)['output_allowed'])
        for params in (dict(pivot_duty_cap_pct=90), dict(turn_assist_enabled=True), dict(turn_drive_duty_pct=60),
                       dict(motor_output_enabled=True), {}):
            with self.assertRaises(ConfigError):
                turn_onsite_update(file_cfg, file_cfg, params)

    def test_onsite_cannot_enable(self):
        file_cfg = ExecutorConfig()
        with self.assertRaises(ConfigError):
            turn_onsite_update(file_cfg, file_cfg, dict(turn_assist_enabled=True))


class Requests(unittest.TestCase):
    def test_request_validation(self):
        self.assertEqual(REQ.problems(), [])
        self.assertEqual(REQ.sign, 1)
        self.assertEqual(replace(REQ, direction='right').sign, -1)
        for kw in (dict(direction='up'), dict(row_width_m=2.), dict(x_c_m=4.), dict(headland_depth_m=3.),
                   dict(reach_m=1.8), dict(row_heading_error_deg=45.), dict(operator_aligned=1),
                   dict(creep_max_m=float('inf'))):
            self.assertTrue(replace(REQ, **kw).problems(), kw)

    def test_headland_depth_rule_refuses_start(self):
        ta = TurnAssist(T.turn_config())
        with self.assertRaises(TurnRequestError) as ctx:
            ta.start(0., replace(REQ, headland_depth_m=3.3), motor_output=True)
        self.assertTrue(any('headland_depth_m' in p for p in ctx.exception.problems))
        self.assertEqual(ta.phase, 'IDLE')

    def test_observe_never_with_output(self):
        ta = TurnAssist(T.turn_config())
        with self.assertRaises(TurnRequestError):
            ta.start(0., REQ, motor_output=True, observe=True)


if __name__ == '__main__':
    unittest.main()
