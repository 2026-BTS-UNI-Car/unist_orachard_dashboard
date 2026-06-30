# Fan Control

## 목적

Fan Control 페이지는 Jetson Orin Nano의 현재 온도를 보면서 `nvfancontrol` 자동 제어와 수동 PWM 제어를 전환하기 위한 화면입니다.

표시 항목:

- 최대 온도
- 자동/수동 토글
- 현재 PWM 값
- Manual PWM 슬라이더
- 빠른 PWM 버튼: `80`, `160`, `255`

## 동작 방식

자동 모드에서는 호스트 `nvfancontrol` 서비스를 시작합니다. 이때 Dashboard는 PWM 값을 유지하려고 다시 쓰지 않습니다.

수동 모드나 PWM 값을 적용하면 서버는 호스트의 `nvfancontrol`을 중지하고 `/sys/class/hwmon/.../pwm1`에 값을 씁니다.

적용한 값은 `data/fan_state.json`에 저장됩니다. Jetson이나 다른 프로세스가 PWM을 바꾸면 dashboard server가 저장된 값을 다시 적용해서 사용자 설정을 유지합니다.

## sysfs 탐색

Dashboard는 `HOST_SYS` 환경 변수를 우선 사용하고, 없으면 `/sys`를 기준으로 팬 hwmon을 찾습니다.

탐색 대상:

- `$HOST_SYS/class/hwmon/hwmon*/name`
- 이름이 `pwmfan` 또는 `pwm-fan`인 hwmon
- fallback: `$HOST_SYS/devices/platform/*pwm*fan*/hwmon/hwmon*`

사용 대상:

- `pwm1`
- `pwm1_enable`이 있으면 함께 사용

## 안전 제한

- 온도 50°C 이상: PWM 0 적용 제한
- 온도 75°C 이상: PWM 160 미만 적용 제한
- 온도 80°C 이상: PWM 255 권장

## Jetson 검증

```bash
cat /sys/class/hwmon/hwmon*/name
systemctl is-active nvfancontrol
cat /sys/class/hwmon/hwmon0/pwm1
curl https://bts.sarihub.com/api/fan/status
```

수동 PWM 확인:

```bash
curl -X POST https://bts.sarihub.com/api/fan/pwm \
  -H 'Content-Type: application/json' \
  --data '{"pwm":160}'
```

자동 모드 전환:

```bash
curl -X POST https://bts.sarihub.com/api/fan/mode \
  -H 'Content-Type: application/json' \
  --data '{"auto":true}'
```
