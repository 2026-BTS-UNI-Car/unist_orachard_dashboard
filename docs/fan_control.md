# Fan Control

## 목적

Fan Control 페이지는 Jetson Orin Nano의 `nvfancontrol` 자동 제어 상태와 수동 PWM 제어를 한 화면에서 확인하기 위한 운영 도구입니다.

표시 항목:

- Fan RPM feedback
- 최대 온도
- 제어 모드: `auto`, `manual`
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
test -e /sys/class/hwmon/hwmonX/pwm1_enable && echo 1 | sudo tee /sys/class/hwmon/hwmonX/pwm1_enable
```

수동 PWM API는 자동 모드가 active여도 먼저 `nvfancontrol`을 중지한 뒤 PWM을 씁니다. 사용자가 지정한 수동 PWM은 `data/fan_state.json`에 저장되고, Jetson이나 컨테이너가 값을 다시 바꾸면 대시보드가 다음 telemetry 주기에서 저장된 PWM으로 재적용합니다.

자동 모드로 되돌리려면 Fan Control 페이지에서 `자동 제어` 토글을 다시 켭니다. 이때 저장 상태도 `auto`로 바뀌고 호스트 `nvfancontrol`이 다시 시작됩니다.

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
test -e /sys/class/hwmon/hwmonX/pwm1_enable && echo 1 | sudo tee /sys/class/hwmon/hwmonX/pwm1_enable
echo 160 | sudo tee /sys/class/hwmon/hwmonX/pwm1
cat /sys/class/hwmon/hwmonX/fan1_input
```

Docker Compose는 기본적으로 `/sys/class/hwmon`과 `/sys/devices`를 rw로 마운트하고 privileged mode로 실행합니다. 컨테이너 내부에서 자동 모드를 제어할 때는 `nsenter`로 호스트 `nvfancontrol` systemd 서비스를 호출합니다.
