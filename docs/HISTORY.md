# 지난 기록이 있는 곳

이 레포의 `docs/`에는 지금 쓰는 문서만 둔다. 2026-11-13 정리 때 그 시점의 보고 문서, 배포·검증 영수증 JSON, 7월 그림, 옛 체크리스트를 main에서 내렸다. 지운 것이 아니라 태그에 그대로 남아 있다. 지금 문서 목록은 [README_KO.md](README_KO.md)에 있다.

## 보존 태그

| 태그 | 커밋 | 무엇 |
| --- | --- | --- |
| `archive-before-cleanup-20261113` | `8b255e7` | 이 정리 직전의 트리. 아래 목록의 파일이 모두 들어 있다 |
| `baseline-installed-20261025` | `b4746cd` | 2026-10-25 차에 설치된 판을 기준으로 맞춘 트리 |
| `main-before-ui-20261112` | `3372139` | 화면 개편을 시작하기 전의 main(차동 구동 시절의 마지막 main) |

태그가 원격에 올라가 있는지는 `git ls-remote --tags origin`으로 확인한다.

## 읽는 방법

```bash
# 태그에 있던 docs 파일 목록
git ls-tree -r --name-only archive-before-cleanup-20261113 docs

# 파일 하나 보기 (예: PWM–속도 분석)
git show archive-before-cleanup-20261113:docs/GPS_PWM_SPEED_AUDIT_20261004.md

# 정리 전 트리 전체를 따로 풀어 보기 (작업 트리는 건드리지 않음)
git archive archive-before-cleanup-20261113 | tar -x -C /tmp/ubts-dashboard-20261113
```

GitHub에서는 `https://github.com/2026-BTS-UNI-Car/unist_orachard_dashboard/blob/archive-before-cleanup-20261113/<경로>`로 열린다. 남은 문서 안의 옛 근거 링크는 이 주소를 가리킨다.

## 내린 것 (2026-11-13)

| 종류 | 개수 | 크기 | 까닭 |
| --- | ---: | ---: | --- |
| 이름에 날짜가 붙은 보고 문서(md) | 61 | 약 0.5 MB | 그 시점의 설치·검증 보고. 지금 코드와 차량(앞바퀴 조향)과 맞지 않는 내용이 섞여 있다 |
| 날짜는 없지만 한 번의 점검·변경 보고였던 문서 | 2 | 12 KB | `gps_pin_inspection.md`(2026-09-26 UART 핀 점검, GPS는 지금 USB 연결), `live_mapping_autonomy_speed.md`(2026-10-02 지도 변경 보고) |
| 배포·검증 영수증 JSON | 40 | 약 0.37 MB | 그 시점의 실행 결과. 코드나 화면이 읽지 않는다 |
| 7월 그림 `docs/assets/` | 23 | 약 17.5 MB | 영상 인식 시절의 그림. 남은 문서가 쓰지 않는다(정리 전 레포 30.6 MB 의 약 57 %) |
| 맨 위 `todo.md`, `RAW_DEPLOYMENT.md` | 2 | 약 59 KB | 2026-07 영상 인식 시절 체크리스트, 2026-10-02 배포 기록 모음 |
| 참조 없는 코드와 그림 | 7 | 약 0.33 MB | `dashboard_server/lidar_odometry.py`, `scripts/render_lidar_odometry_audit.py`, `scripts/import_video_archive.py`, `scripts/validate_dashboard_ui.py`, `scripts/validate_video_archive_ui.py`, `scripts/validate_docs_ui.py`, `static/assets/ubts-logo.png` |

### 보고 문서 목록 (주제별)

- 지도·위치 추정(native 관찰, 지도 전송·속도), 27개: CONTINUOUS_REPLAY_STATUS_20261005, INTEGER_GYRO_COVERAGE_CHECKPOINT_20261008, INTEGRATED_MAPPING_STATUS_20261004, MAPPING_OPERATIONS_20261006, MAPPING_RASTER_PERFORMANCE_20261007, MAP_DELIVERY_STATUS_20261005, MAP_GEOMETRY_STATUS_20261004, MAP_SPEED_ACCURACY_20261005, NATIVE_ADMISSION_PROGRESS_20261010, NATIVE_ASYNC_MAP_INSTALL_20261010, NATIVE_CALLBACK_RECOVERY_20261010, NATIVE_COMBINED_RECOVERY_PROGRESS_20261010, NATIVE_DEADLINE_CHECKPOINT_20261010, NATIVE_FINAL_SCALAR_RECOVERY_PROGRESS_20261010, NATIVE_GNSS_INPUT_20261010, NATIVE_IMU_GAP_PROGRESS_20261010, NATIVE_INPUT_RECOVERY_PROGRESS_20261010, NATIVE_JPEG_VFR_COMPATIBILITY_20261007, NATIVE_LATENCY_PROGRESS_20261010, NATIVE_MAP_CONTINUITY_20261013, NATIVE_POSTCONSUMED_RECOVERY_PROGRESS_20261010, NATIVE_POWER_CUT_CHECKPOINT_20261010, NATIVE_TILE_CHECKPOINTS_20261004, REPLAY_MAP_STATUS_20261004, map_cell_encoding_20261009, native_map_gap_fence_default_OFF_supervisor_20261010, native_sensor_shadow_20261008
- 녹화·영상·카메라, 10개: ARCHIVE_LIGHT_20261024, ARCHIVE_LIGHT_VERIFICATION_20261024, ARCHIVE_MEDIA_QUEUE_20261007, ARCHIVE_SHARED_RENDER_CLOCK_20261007, CAMERA_ACQUISITION_CADENCE_20261007, CAMERA_MJPEG_PASSTHROUGH_20261013, PENDING_METADATA_REFRESH_20261024, RECORDED_PREVIEW_RECOVERY_20261007, RECORDING_DURABILITY_SIM_STOP_20261010, RECORDING_START_CLOCK_QUALITY_20261007
- 현장 자료·주행 응답·자율(차동 구동 시절), 11개: AUTONOMY_SOFTWARE_DELIVERY_20261010, ENCODERLESS_AUTONOMY_FIELD_PLAN_20261004, ENCODERLESS_DRIVE_RESPONSE_20261013, FIELD_AUTONOMY_PREPARATION_20261007, FIELD_DATA_AUDIT_20261003, FIELD_RAW_BENCHMARK_20261004, FIELD_REQUIREMENTS_REVIEW_20261004, GPS_PWM_SPEED_AUDIT_20261004, LEGACY_GNSS_EVIDENCE_20261014, REAL_VEHICLE_AUTONOMY_DELIVERY_20261009, orchard_fusion_v9_20260721
- 배포·운영·화면, 11개: CHECKPOINT_FRAME_UI_20261002, GPS_RENDER_UPDATES_20261007, RESUME_OPERATIONS_20261013, SENSOR_LATENCY_STATUS_20261004, VERIFIED_UI_RELEASE_20261024, dashboard_server_lifecycle_20261024, esp_deployment_20260926, gps_map_initialization_recovery_20261024, gps_map_recovery_deployment_20261024, sensor_ws_backpressure_20261024, sensor_ws_deployment_20261024
- 작업 재개용 긴 메모 2개(2026-10-13, 2026-10-14). 파일 이름에 작업 보조 도구 이름이 들어 있어 여기에는 이름을 적지 않는다. 태그의 `docs/` 목록에서 찾는다

수치가 남아 있어 다시 볼 만한 것: PWM과 속도의 관계는 GPS_PWM_SPEED_AUDIT_20261004(.md, .json)와 ENCODERLESS_DRIVE_RESPONSE_20261013, 현장 기록의 목록과 상태는 FIELD_DATA_AUDIT_20261003, 1080p 원본 녹화의 원인과 fps는 CAMERA_MJPEG_PASSTHROUGH_20261013, 녹화 저장의 fsync 방식은 RECORDING_DURABILITY_SIM_STOP_20261010.

### 영수증 JSON 목록

ap_ip_access_20261004, camera_preview_validation, cell_mapping_deployment_20261004, field_followup_deployment_20261004, field_improvement_evidence_20261004, field_map_history_deployment_20261003, field_raw_benchmark_20261004, field_requirements_review_20261004, field_rollout_deployment_20261004, gps_map_recovery_deployment_receipt_20261024, gps_pwm_speed_audit_20261004, gps_reception_recording_validation, imu_contract_validation, imu_prepare_hold, imu_raw_removal_validation, jetson_camera_preview_deployment, jetson_chartless_render_validation, jetson_executor_pacing_validation, jetson_four_sensor_observation, jetson_gps_antenna_diagnosis, jetson_gps_reception_diagnosis, jetson_gps_reception_recording_deployment, jetson_gps_reconnect_deployment, jetson_imu_contract_deployment, jetson_imu_raw_removal_deployment, jetson_imu_recording_label_deployment, jetson_lidar_redraw_validation, jetson_live_map_browser_20261002, jetson_live_map_deployment_20261002, jetson_motor_recording_validation, jetson_nodelay_validation, jetson_recorder_ui_freshness_deployment, jetson_recorder_ui_polling_deployment, jetson_usb_gps_deployment, low_bandwidth_playback_20261004, map_speed_accuracy_20261005, native_tile_checkpoints_20261004, sim_field_stress_runtime_20261004, windows_gps_placement_comparison, windows_gps_reception_diagnosis (모두 `.json`)
