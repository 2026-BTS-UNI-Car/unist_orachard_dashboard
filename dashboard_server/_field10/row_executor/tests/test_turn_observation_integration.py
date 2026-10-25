"""Small local checks for INFO_ONLY integration; no ROS, Docker, socket or motor I/O."""
import io
import json
import types
import unittest
from unittest.mock import patch

import rowexec_testlib  # noqa: F401
from row_executor import executor_main as M
from row_executor import operator_http as H
from row_executor.config import ExecutorConfig
from row_executor.core import ENDED, Decision
from row_executor.turn_observation import TurnObservationRunner
from test_executor_loop import motor_config


PARAMS = dict(direction='left', row_width_m=4., x_c_m=2., reach_m=4.5,
              headland_depth_m=5., operator_aligned=True)


class ObserveIntegrationTest(unittest.TestCase):
    def make_loop(self, cfg=None, **kw):
        def forbidden():
            self.fail('an observation created a bridge')
        return M.ExecutorLoop(cfg or ExecutorConfig(), clock=lambda: 100., wall=lambda: 0.,
                              bridge_factory=forbidden, **kw)

    def test_real_assist_is_passive_arm_refused_stop_has_no_transport(self):
        loop = self.make_loop()
        result = loop._req_turn_observe(100., params=PARAMS)
        self.assertTrue(result['ok'])
        self.assertFalse(result['output_allowed'])
        self.assertTrue(loop.runner.assist.observe)
        self.assertFalse(loop.runner.assist.motor_output)
        self.assertFalse(loop._req_arm(100.)['ok'])
        decision = loop.step(100.)
        self.assertEqual((decision.left, decision.right), (0, 0))
        status = loop.status()
        self.assertEqual(status['vehicle_control'], 'none')
        self.assertFalse(status['turn_output_allowed'])
        self.assertFalse(status['turn_observe']['arm_available'])
        self.assertNotIn('turn_feasibility', status['turn_observe'])
        self.assertIsNone(loop.bridge)
        self.assertIsNone(loop._stop_transport)
        self.assertIsNone(loop.emergency_stop('test')['stop_sent'])
        loop.step(100.05)
        self.assertEqual(loop.runner.state, ENDED)
        self.assertTrue(any(r.get('event') == 'turn_observe_end' for r in loop.log.records))

    def test_row_and_observation_are_mutually_exclusive_and_can_switch_after_end(self):
        loop = self.make_loop()
        self.assertTrue(loop._req_turn_observe(100., params=PARAMS)['ok'])
        run = loop.runner.run_id
        self.assertEqual(loop._req_start(100., row_len_m=10.)['error'], 'run_in_progress')
        self.assertEqual(loop._req_turn_observe(100., params=PARAMS)['error'], 'run_in_progress')
        self.assertTrue(loop._req_end(100.1)['ok'])
        self.assertTrue(loop._req_start(100.2, row_len_m=10.)['ok'])
        self.assertNotIsInstance(loop.runner, TurnObservationRunner)
        self.assertGreater(loop.runner.run_id, run)
        self.assertEqual(loop._req_turn_observe(100.2, params=PARAMS)['error'], 'run_in_progress')

    def test_motor_process_and_shadow_auto_refuse_observation(self):
        for loop in (self.make_loop(motor_config(), motor_output_flag=True),
                     self.make_loop(shadow_auto=True)):
            self.assertEqual(loop._req_turn_observe(100., params=PARAMS)['error'],
                             'turn_observe_requires_manual_shadow')
            self.assertNotIsInstance(loop.runner, TurnObservationRunner)

    def test_request_cannot_override_output_or_observe_flags(self):
        for name, value in (('motor_output', True), ('observe', False), ('turn_assist_enabled', True),
                            ('allow_next_absent', True), ('pivot_only', True)):
            loop = self.make_loop()
            result = loop._req_turn_observe(100., params=dict(PARAMS, **{name: value}))
            self.assertFalse(result['ok'], name)
            self.assertIsNone(loop.bridge)

    def test_adapter_clamps_an_underlying_nonzero_and_ends(self):
        loop = self.make_loop()
        self.assertTrue(loop._req_turn_observe(100., params=PARAMS)['ok'])
        assist = loop.runner.assist
        with patch.object(assist, 'tick', return_value=Decision(60, -60, 'OBSERVE', 'bad', False, {})):
            d = loop.step(100.)
        self.assertEqual((d.left, d.right, d.state), (0, 0, ENDED))
        self.assertEqual(d.reason, 'observation_output_invariant')
        self.assertIsNone(loop.bridge)

    def test_default_constructor_does_not_start_or_enable_observation(self):
        loop = self.make_loop()
        loop.step(100.)
        self.assertNotIsInstance(loop.runner, TurnObservationRunner)
        self.assertFalse(loop.config.motor_output_enabled)
        self.assertFalse(loop.config.calib_motor_output_enabled)
        self.assertFalse(loop.config.turn_assist_enabled)
        self.assertFalse(loop.status()['turn_output_allowed'])

    def test_watchdog_pause_ends_observation_and_never_enables_motor(self):
        loop = self.make_loop()
        self.assertTrue(loop._req_turn_observe(100., params=PARAMS)['ok'])
        loop.runner.pause(100.1, 'stall_guard')
        d = loop.step(100.1)
        self.assertEqual(d.state, ENDED)
        self.assertEqual((d.left, d.right), (0, 0))
        self.assertFalse(loop.motor_allowed)

    def test_page_labels_observation_as_info_only(self):
        self.assertIn('보조 회전 관찰 · INFO_ONLY · 모터 허가 없음', H.PAGE)
        self.assertIn('/api/turn_observe', H.PAGE)
        self.assertIn('관찰 종료는 차량을 정지시키지 않는다', H.PAGE)

    def test_http_route_rejects_output_options_and_dispatches_only_observe(self):
        loop = self.make_loop()
        host = '127.0.0.1:8097'
        for body, wanted_code in ((dict(PARAMS, motor_output=True), 400), (PARAMS, 200)):
            handler = object.__new__(H.Handler)
            raw = json.dumps(body).encode()
            handler.path = '/api/turn_observe'
            handler.headers = {'Host': host, 'Origin': 'http://' + host,
                               'X-Row-Token': 'test-token-0123456789abcdef', 'Content-Length': str(len(raw))}
            proxy = types.SimpleNamespace(request=lambda kind, **kwargs: getattr(loop, '_req_' + kind)(100., **kwargs))
            handler.server = types.SimpleNamespace(host_header=host, origin='http://' + host,
                                                  token='test-token-0123456789abcdef', loop=proxy)
            handler.rfile = io.BytesIO(raw)
            responses = []
            handler._send = lambda code, data, **kw: responses.append((code, data))
            handler.do_POST()
            self.assertEqual(responses[0][0], wanted_code)
        self.assertTrue(loop.runner.assist.observe)
        self.assertFalse(loop.runner.assist.motor_output)
        self.assertIsNone(loop.bridge)


if __name__ == '__main__':
    unittest.main()
