#!/usr/bin/env python3
"""Pick the ESP control bridge by ROVER_ESP_KIND (python3 -m scripts.esp_rover_bridge).

  esp8266_v2 (default)  scripts.rover_control_bridge: the differential V2 bridge, unchanged
  esp32_abs_hold        scripts.esp32_steer_bridge: the ESP32-S3 'ABS-HOLD-8-REBOUND-LIMITS' firmware with three
                        BTS7960 drivers (front steering, left and right drive), front_steer control protocol
The default service in docker-compose.yml still starts scripts.rover_control_bridge directly; this entry point is for
installations that choose the bridge by the environment.
"""
import importlib
import os
import sys

KINDS = {
    'esp8266_v2': 'scripts.rover_control_bridge',
    'esp32_abs_hold': 'scripts.esp32_steer_bridge',
}
DEFAULT_KIND = 'esp8266_v2'


def module_for(kind):
    kind = (kind or DEFAULT_KIND).strip()
    if kind not in KINDS:
        raise ValueError('ROVER_ESP_KIND %r: expected one of %s' % (kind, ', '.join(sorted(KINDS))))
    return KINDS[kind]


def main():
    try:
        name = module_for(os.environ.get('ROVER_ESP_KIND'))
    except ValueError as exc:
        print('esp_rover_bridge: %s' % exc, file=sys.stderr)
        return 2
    result = importlib.import_module(name).main()
    return result if isinstance(result, int) else 0


if __name__ == '__main__':
    sys.exit(main())
