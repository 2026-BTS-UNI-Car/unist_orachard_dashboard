# Dashboard native ROS 2 IMU display and recording

The BNO085 driver remains the sole I2C owner (Jetson I2C-7, address 0x4A).
Device-native rotation-vector fusion, quaternion normalization, SI units and
warm-up rejection remain unchanged. The dashboard does not open I2C.

## Current topic contract

| Topic | Publisher | Meaning |
|---|---|---|
| `/imu/data_raw` | `ubts_bno085_imu_driver` | Native quaternion, angular velocity (rad/s), acceleration including gravity (m/s2) |
| `/imu/mag` | `ubts_bno085_imu_driver` | Native magnetic field in Tesla |
| `/imu/data` | `ubts_data_recorder` | Unmodified compatibility relay of the complete raw Imu message |
| `/orchard/imu/driver_status` | `ubts_bno085_imu_driver` | Sensor/transport status and unverified mount convention |

`/imu/data` preserves the exact input header/frame/stamp, quaternion sign,
angular velocity, acceleration and all nine entries of each covariance matrix.
It is not a base_link estimate. The relay works while idle and while recording;
there is no stationary warm-up or reference reset in the recorder. Raw and relay
topic names must differ to avoid a feedback loop.

Software yaw offsets (previously 90 degrees), stationary leveling, recording-local
world/heading references, gyro-bias subtraction and magnetic-vector rotation have
been removed. The BNO085's own native fusion is not disabled or reconfigured.
No software extrinsic or new noise model is substituted for the removed logic.
Physical IMU-to-base axes remain unverified until independently measured.

The bridge subscribes only to raw IMU and magnetic field. The live UI always
shows raw values, even if an old process/status object contains corrected data.
Euler conversion is display-only and never changes the ROS message. An unavailable
orientation is not displayed as a fabricated zero heading. Each measurement group
keeps its ROS availability flag: covariance[0] == -1 is unavailable; all-zero
covariance is unknown uncertainty, not perfect accuracy. Historical -1 values
are never upgraded automatically.

## Retired settings and actions

The manual-zero button is removed. POST `/api/imu/zero` and recorder POST
`/imu/zero` return HTTP410 with `ok:false, state:removed`; they do not contact a
sensor, create a reference, write settings or modify recorder state.

Compose no longer supplies IMU calibration/stationarity/yaw options. The output
setting is `IMU_RELAY_TOPIC` / `--imu-relay-topic` (default `/imu/data`). Retired
`--imu-calibration-*`, `--imu-stationary-*` and `--imu-body-yaw-offset-deg` arguments
must be removed from any external Compose command override before deployment.
The bridge no longer subscribes to `/orchard/imu/calibration` or `/imu/data`.

## Recordings and historical compatibility

New recordings keep `timeline.json` IMU `raw` samples, original ROS stamps/frame,
full covariance arrays, nearby native magnetic samples, and explicit
`processing.mode=sensor_native_raw` / `revision=sensor_native_raw_unmodified_v1`.
`corrected` is null; no calibration estimate, mount transform, bias, or fake
corrected sample is generated. New recordings do not create `imu_calibration.json`
and do not record `/orchard/imu/calibration`. Both raw and compatibility relay
ROS streams are retained in rosbag for existing consumers; they are duplicates,
not independent measurements. IMU recording remains enabled by default.

Existing recordings are not edited, deleted or relabeled. Historical frames with
recorded `corrected` data still replay that exact sample with the existing
start-corrected label and archived provenance. A new raw-only frame replays raw.
Receipt-based timeline t_s remains a preview clock; ROS header stamps are the
native sensor-time source for later synchronization.

### LiDAR source timing 보존

회전 중 2D LiDAR 한 sweep을 순간 촬영으로 취급하면 관측이 왜곡된다. recorder는 ROS header와 `scan_time_s`, `time_increment_s`, `angle_min_rad`, `angle_increment_rad`, 기존 `point_time_direction=-1.0`을 보존한다. 마지막 값은 발행 배열 방향에 관한 과거 가정이며 광선별 실제 획득 시각의 검증값이 아니다.

2026-09-30 읽기 전용 확인에서 실제 `sllidar_node`는 `angle_compensate=true`, `inverted=false`, `DenseBoost`다. 컨테이너 소스 `34300099fadfc772965962dec837bf436706188f`는 거리값을 각도 칸에 재배치/복제하고, `grabScanDataHq` 호출 전후의 호스트 시각으로 header와 duration을 만든다. 따라서 보관 배열의3,240칸은 서로 독립적인 원시 광선3,240개의 정확한 시간열을 뜻하지 않는다. 해당 소스와 실행 바이너리의 빌드 동일성은 별도 검증 대상이다.

현재 대시보드의 카메라는 표시·기록 전용이며 실제 대시보드에는 SIM occupancy 처리를 배포하지 않았다. 과거 renderer의 고정 duration fallback이나 yaw-only deskew는 encoderless 주행의 검증 근거가 아니다. 새 입력 검사에서는 원본 시각을 보존하되 rolling scan을 순간 스캔으로 수용하지 않는다. 드라이버의 획득 순서·시각 지연, IMU 장착, 회전·이동 보정을 검증하기 전에는 자동으로 deskew를 켜지 않는다.

## Local verification

Run the native-payload/bridge tests on Windows and the recorder/wire/API tests
inside an isolated ROS2 container with no network or devices. Wire tests compare
serialized input/output bytes, including nonzero off-diagonal covariances and
180-degree quaternions. `node tests/test_imu_raw_ui.js` exercises live raw selection
and both historical corrected/new raw archive selection. No GitHub Actions run
is needed. Code/test completion is separate from actual Jetson deployment.
