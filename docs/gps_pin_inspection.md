# Jetson GPS 전체 핀 원격 점검

점검: 2026-09-26T13:04:37+09:00. NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super, L4T 36.5.
사용자가 현장에서 배선이나 제품을 볼 수 없는 상태에서 **J12 40핀 전체**를 확인했다.
GPS가 어느 핀에 연결됐는지는 아직 식별하지 못했다.

## 확인 결과

- 40핀 = 전원 4개 + GND 8개 + UART 2개 + I2C 4개 + GPIO 입력 22개.
- UART 8/10번은 ttyTHS1, Linux I2C는 3/5번이 버스 7, 27/28번이 버스 1이다.
- 이전 기록의 “27/28번 = 버스 0” 가정은 잘못됐다. 실제 pinmux와 device-tree 별칭을
  대조해 수정했고 **버스 1/7 모두** GNSS 기본 주소 0x42의 bytes-available 레지스터
  0xfd를 조회했다. 두 버스 모두 ACK가 없었다. 사용자 변경 주소까지 배제한 것은 아니다.
- GPIO 입력 22개는 30.0초 동안 각 91,420회 읽었다.
  모든 표본의 입력 레지스터가 LOW였으며 관찰된 전이는 0회다.
- UART는 9600–921600의 8가지 속도에서 UBX/NMEA 조회를 계속하지만 유효 응답이 없다.
- 이 조사에서 GPIO/핀mux/전원 설정을 변경하거나, 모터를 구동하거나, 재부팅하지 않았다.
  입력 관찰 전후 pinconf 해시가 동일함을 확인했다.

## 40핀별 결과

아래 번호는 SoC GPIO 번호가 아니라 **J12 물리 핀 번호**다.

| 핀 | 기능 / 경로 | SoC 패드 | 방향 / 설정 | 관측 결과 |
|---:|---|---|---|---|
| 1 | 3.3V | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 2 | 5V | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 3 | I2C SDA | gen8_i2c_sda_pdd2 | 입력 버퍼 ON | Linux 버스 7: GNSS 0x42 응답 없음 |
| 4 | 5V | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 5 | I2C SCL | gen8_i2c_scl_pdd1 | 입력 버퍼 ON | Linux 버스 7: GNSS 0x42 응답 없음 |
| 6 | GND | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 7 | GPIO 입력, 미점유 | soc_gpio59_pac6 | 입력 버퍼 ON / pull-up | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 8 | UART TX | uart1_tx_pr2 | UART 출력 | 커널 송신 처리 확인; 핀 전압 미측정 |
| 9 | GND | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 10 | UART RX | uart1_rx_pr3 | 입력 버퍼 ON | ttyTHS1 수신 0 bytes |
| 11 | GPIO 입력, 미점유 | uart1_rts_pr4 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 12 | GPIO 입력, 미점유 | soc_gpio41_ph7 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 13 | GPIO 입력, 미점유 | spi3_sck_py0 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 14 | GND | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 15 | GPIO 입력, 미점유 | soc_gpio39_pn1 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 16 | GPIO 입력, 미점유 | spi3_cs1_py4 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 17 | 3.3V | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 18 | GPIO 입력, 미점유 | spi3_cs0_py3 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 19 | GPIO 입력, 미점유 | spi1_mosi_pz5 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 20 | GND | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 21 | GPIO 입력, 미점유 | spi1_miso_pz4 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 22 | GPIO 입력, 미점유 | spi3_miso_py1 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 23 | GPIO 입력, 미점유 | spi1_sck_pz3 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 24 | GPIO 입력, 미점유 | spi1_cs0_pz6 | 입력 버퍼 ON / pull-up | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 25 | GND | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 26 | GPIO 입력, 미점유 | spi1_cs1_pz7 | 입력 버퍼 ON / pull-up | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 27 | I2C SDA | gen2_i2c_sda_pdd0 | 입력 버퍼 ON | Linux 버스 1: GNSS 0x42 응답 없음 |
| 28 | I2C SCL | gen2_i2c_scl_pcc7 | 입력 버퍼 ON | Linux 버스 1: GNSS 0x42 응답 없음 |
| 29 | GPIO 입력, 미점유 | soc_gpio32_pq5 | 입력 버퍼 ON / pull-up | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 30 | GND | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 31 | GPIO 입력, 미점유 | soc_gpio33_pq6 | 입력 버퍼 ON / pull-up | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 32 | GPIO 입력, 미점유 | soc_gpio19_pg6 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 33 | GPIO 입력, 미점유 | soc_gpio21_ph0 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 34 | GND | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 35 | GPIO 입력, 미점유 | soc_gpio44_pi2 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 36 | GPIO 입력, 미점유 | uart1_cts_pr5 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 37 | GPIO 입력, 미점유 | spi3_mosi_py2 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 38 | GPIO 입력, 미점유 | soc_gpio43_pi1 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |
| 39 | GND | — | 전원/접지 | 정격/배치 확인, 실제 전압·접속 여부 미측정 |
| 40 | GPIO 입력, 미점유 | soc_gpio42_pi0 | 입력 버퍼 ON / pull-down | 입력 레지스터 LOW 91,420회 / 변화 0회 |

## 해석 범위

`unused` 또는 `미점유`는 GPIO 소비자가 없다는 소프트웨어 상태이며 배선 부재를 뜻하지 않는다.
GPIO 레지스터 LOW도 전선이 없거나 GPS가 고장났다는 증거로 취급하지 않았다.
내부 pull-up이 설정된 7/24/26/29/31번도 읽은 값은 LOW였다. 회로 부하, 레벨 변환,
외부 연결 등을 실측하지 않았으므로 이 차이의 원인은 확정할 수 없다.

샘플링은 핀당 평균 약 3.05 kHz다.
Linux 스케줄링 간격이 변하므로 짧은 펄스를 놓칠 수 있고 UART 비트열을 복호화한 검사는 아니다.
전원/GND 핀은 GPIO로 읽거나 ADC로 전압 측정할 수 없다. 표의 3.3V/5V는 정격 명칭이다.

UART/I2C는 주변장치 기능(SFIO)으로 동작하고 해당 GPIO 경로는 비활성화되어 있다.
그 상태에서 얻은 GPIO 입력 레지스터 값은 실제 선의 전압으로 해석하지 않고, UART 드라이버
카운터와 I2C 조회 결과를 사용했다. TX는 입력 버퍼도 꺼져 있다.

메모리 접근은 `/dev/mem`을 O_RDONLY, mmap을 PROT_READ로 열어 문서화된 GPIO
설정/입력 레지스터만 읽었다. gpio 요청, gpioget, 출력 토글, pull 변경은 하지 않았다.
읽기 경로는 기존 Force Recovery/PCIe wake/Power 입력과 regulator 출력의 HIGH 값이
커널 GPIO 상태와 일치하는 것으로 교차 확인했다. 설정 레지스터 값은 gpioinfo 방향과 대조했다.

## 다른 경로

- J14 디버그 UART(3/4번)는 ttyTCU0 콘솔이며 getty가 활성화되어 있다. GPS로 재설정하지 않았다.
- ttyTHS2는 Orin Nano의 실제 외부 UART 핀으로 연결되지 않는 serial@3140000이다.
- ESP 펌웨어 소스에서 D1/D2/D6/D7은 모터 전원, D8/D5/D3/D0은 방향 PWM이다.
  D4는 모터 명령 대상에서 제외되고 GPS 수신 코드는 없다. 이는 펌웨어 소스 확인이며
  ESP 각 핀의 실제 전압을 측정한 결과는 아니다. ESP 펌웨어를 바꾸지 않았다.
- IMU 드라이버의 현재 I2C 버스는 7, 주소는 0x4A이다.
  IMU 주소를 대상으로 GPS 조회를 보내지 않았다.

## 남은 미확인 사항

GPS 보드의 실제 전원 전압, 공통 GND, TX/RX 연결 위치, UART1/UART2 선택 및 수신기 저장
설정은 아직 미확인이다. 이번 조사에서 GPS로 식별할 수 있는 응답이나 GPIO 신호는 얻지 못했다.
GPS 연결 완료 또는 모든 핀의 전기적 정상 판정을 의미하지 않는다.

## 근거

- [NVIDIA Orin Nano carrier board specification, J12/J14](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/orin_nano/docs/jetson_orin_nano_devkit_carrier_board_specification_sp.pdf)
- [NVIDIA: 27/28번 GEN2 I2C의 c240000 매핑](https://forums.developer.nvidia.com/t/jetson-orin-nano-i2c-connect-tca9555/314144)
- [NVIDIA: Linux I2C 7/1 매핑 설명](https://forums.developer.nvidia.com/t/i2c-bus-number-changed-unexpectedly-on-jetson-orin-nano/326222)
- [NVIDIA 작성 Linux Tegra GPIO 드라이버: 레지스터와 Tegra234 bank/port 표](https://github.com/torvalds/linux/blob/v6.8/drivers/gpio/gpio-tegra186.c)
- 실제 보드의 Jetson-IO, device tree, pinconf-groups, gpioinfo 및 UART 상태.
- 원본 관측 JSON은 작업 공간 `diagnostics/gps-pins-20260926/observations.json`에 보관했다.
