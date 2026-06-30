# Fan Control

## 목적

Fan Control 페이지는 Jetson Orin Nano의 `nvfancontrol` 자동 제어 상태와 수동 PWM 제어를 한 화면에서 확인하기 위한 운영 도구입니다.

표시 항목:

- Fan RPM feedback
- 최대 온도
- 제어 모드: `auto`, `manual`, `unknown`
- PWM 값: 0-255
- PWM/RPM 차트
- PWM-RPM calibration table

## sysfs 탐색

Dashboard는 `HOST_SYS` 환경 변수를 우선 사용하고, 없으면 `/sys`를 기준으로 팬 hwmon을 찾습니다.

탐색 대상:

- `$HOST_SYS/class/hwmon/hwmon*/name`
- 이름이 `pwmfan` 또는 `pwm-fan`인 hwmon
- fallback: `$HOST_SYS/devices/platform/**/pwm-fan/hwmon/hwmon*/pwm1`

읽기 대상:

- `pwm1`
- `pwm1_enable`
- `fan1_input`
- `rpm`

## 자동/수동 모드

자동 모드:

```bash
systemctl start nvfancontrol
```

수동 모드:

```bash
systemctl stop nvfancontrol
echo 1 | sudo tee /sys/class/hwmon/hwmonX/pwm1_enable
```

수동 PWM API는 자동 모드가 active이면 값을 쓰지 않습니다.

## Preset

기본 preset:

| Preset | PWM |
| --- | ---: |
| off | 0 |
| quiet | 80 |
| normal | 160 |
| max | 255 |

환경 변수로 override할 수 있습니다.

- `FAN_PWM_OFF`
- `FAN_PWM_QUIET`
- `FAN_PWM_NORMAL`
- `FAN_PWM_MAX`

## PWM-RPM calibration

실제 제어 입력은 PWM이고 RPM은 feedback입니다. PWM과 RPM 관계는 팬/전원/온도에 따라 달라질 수 있으므로 calibration table로 근사합니다.

보정 단계:

```text
0, 40, 60, 80, 100, 128, 160, 192, 224, 255
```

각 PWM을 적용한 뒤 RPM을 읽어 `data/fan_calibration.json`에 저장합니다.

예시:

```json
{
  "created_at": "2026-06-30T12:34:56+09:00",
  "board": "jetson",
  "fan_hwmon_name": "pwmfan",
  "points": [
    {"pwm": 0, "rpm": 0},
    {"pwm": 80, "rpm": 1450},
    {"pwm": 160, "rpm": 3860},
    {"pwm": 255, "rpm": 6120}
  ],
  "min_spin_pwm": 55,
  "max_rpm": 6120
}
```

목표 RPM 모드는 calibration table이 있을 때만 활성화됩니다.

## 안전 제한

- 온도 50°C 이상: fan off 제한
- 온도 75°C 이상: 낮은 PWM 제한
- 온도 80°C 이상: 자동 복귀 또는 PWM 255 권장

## Jetson 검증 명령

```bash
cat /sys/class/hwmon/hwmon*/name
systemctl is-active nvfancontrol
sudo systemctl stop nvfancontrol
echo 1 | sudo tee /sys/class/hwmon/hwmonX/pwm1_enable
echo 160 | sudo tee /sys/class/hwmon/hwmonX/pwm1
cat /sys/class/hwmon/hwmonX/fan1_input
```

Docker로 팬 제어까지 테스트하려면 `docker-compose.jetson-fan.yml`의 rw sysfs mount와 권한을 확인합니다.
