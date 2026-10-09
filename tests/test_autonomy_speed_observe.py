"""Active speed changes fence the observe loop without motor dispatch."""
import unittest
from unittest.mock import Mock
from tests import test_autonomy_observe_api_ui as api_tests


class ActiveObserveSpeedTests(unittest.TestCase):
    def fixture(self, percent):
        helper=api_tests.ObserveAPITests()
        handler=helper.fixture({'speed_percent':percent})
        handler.autonomy_settings.set_speed=Mock(return_value={'ok':True,'speed_percent':percent})
        handler.autonomy_observation.set_cruise_percent=Mock()
        return helper,handler

    def test_active_speed_propagates_without_motor_action(self):
        helper,handler=self.fixture(12)
        handler.autonomy_observation.start(cruise_percent=20)
        self.assertEqual(helper.request(handler,'/api/autonomy/speed',True)[0],200)
        handler.autonomy_observation.set_cruise_percent.assert_called_once_with(12)
        handler.provider.rover_action.assert_not_called()

    def test_over_cap_active_speed_never_persists(self):
        for percent in (31,100,True,None):
            with self.subTest(percent=percent):
                helper,handler=self.fixture(percent)
                handler.autonomy_observation.start(cruise_percent=20)
                self.assertEqual(helper.request(handler,'/api/autonomy/speed',True)[0],400)
                handler.autonomy_settings.set_speed.assert_not_called()
                handler.autonomy_observation.set_cruise_percent.assert_not_called()
                handler.provider.rover_action.assert_not_called()

    def test_idle_existing_preference_does_not_start_work(self):
        helper,handler=self.fixture(70)
        self.assertEqual(helper.request(handler,'/api/autonomy/speed',True)[0],200)
        handler.autonomy_observation.set_cruise_percent.assert_not_called()
        self.assertEqual(handler.autonomy_observation.starts,0)


if __name__=='__main__':unittest.main()
