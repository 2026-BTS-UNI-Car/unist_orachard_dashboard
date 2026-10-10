# 2026-10-10 원본 IMU callback 만료 복구

Jetson의 원본 LiDAR·IMU 입력 처리에서, 양수 지연으로 만료된 IMU callback은 폐기하고 다음 정상 두 스캔과 실제 IMU 쌍부터 이어서 처리하도록 수정했다. 지도를 지우거나 센서 시각을 다시 찍지 않는다. 미래 시각·역행·출처 변경·손상·Stop 오류는 계속 거부한다. source 250ms, RAW 200ms, IMU bracket 60ms는 유지했다.

ROOT에서 관련 50개 회귀 테스트를 별도 실행해 통과했고, 정확한 지도 패키지 연결·잘못된 parent·모듈 변조 거부 3개도 확인했다. 첫 ARM 빌드의 지도 parent 검증 실패는 설치 전에 발견했으며 로그를 보존했다. 실제 설치는 수정한 strict loader로 검증한 후 진행했다.

**아래 결과는 자동 모터 출력을 하지 않은 실제 센서 관찰 시험이다. 자율주행 완료 또는 실제 주행·제동 검증으로 간주하지 않는다.** 기존 WEB와 13개 운영 서비스, 장착·제동 보정은 변경하지 않았다. 바퀴 전원 OFF는 사용자 설명이다. GitHub Actions 대신 직접 SSH를 사용했다.

```json
{
  "test_scope": "120-second actual stationary OBSERVE_ONLY; not autonomous motion",
  "native_CID": "085c726348a3a93ec045ea89e2741903637cd8ac62c7f92eaf5b689e951cd39d",
  "native_image": "sha256:a27f7615a710f4cc5d358b2656611f48927c7bc7e4288826958f4dd270b26ac9",
  "source_manifest_sha256": "e4f7a9335a85c761bd07b6e02019b4633cb1e4856afcd33e528fc2cf1dca1c8c",
  "source_diagnostics": 573,
  "source_failure": null,
  "source_progress": {
    "qualified": false,
    "new_diagnostics": 152,
    "window_seconds": 26.002308750990778,
    "max_progress_gap_seconds": 10.003202987019904,
    "final_progress_gap_seconds": 55.9499469010625
  },
  "sustained_native_input_qualified": false,
  "sustained_mapping_qualified": false,
  "M12_qualified": false,
  "samples_received": {
    "sensor_msgs/msg/LaserScan": 1191,
    "sensor_msgs/msg/Imu": 2379
  },
  "samples_admitted": {
    "sensor_msgs/msg/LaserScan": 991,
    "sensor_msgs/msg/Imu": 2374
  },
  "original_callback_expiry_wait_count": 1,
  "last_original_callback_expiry_wait_stage": "callback_admission",
  "last_original_callback_expiry_wait_ages_ns": {
    "Header_age_ns": 211943536,
    "callback_RAW_age_ns": 210895552,
    "callback_SYSTEM_age_ns": 210977583
  },
  "scan_expiry_wait_count": 0,
  "map_checkpoints_every_10s": [
    {
      "sample": 0,
      "version": 0,
      "cells": 0,
      "adapter_fault": null
    },
    {
      "sample": 10,
      "version": 17,
      "cells": 2056,
      "adapter_fault": null
    },
    {
      "sample": 20,
      "version": 17,
      "cells": 2056,
      "adapter_fault": null
    },
    {
      "sample": 30,
      "version": 20,
      "cells": 2056,
      "adapter_fault": null
    },
    {
      "sample": 40,
      "version": 29,
      "cells": 2058,
      "adapter_fault": null
    },
    {
      "sample": 50,
      "version": 45,
      "cells": 2059,
      "adapter_fault": null
    },
    {
      "sample": 60,
      "version": 58,
      "cells": 2063,
      "adapter_fault": null
    },
    {
      "sample": 70,
      "version": 63,
      "cells": 2064,
      "adapter_fault": null
    },
    {
      "sample": 80,
      "version": 64,
      "cells": 2064,
      "adapter_fault": null
    },
    {
      "sample": 90,
      "version": 69,
      "cells": 2065,
      "adapter_fault": null
    },
    {
      "sample": 100,
      "version": 77,
      "cells": 2068,
      "adapter_fault": null
    },
    {
      "sample": 110,
      "version": 91,
      "cells": 2073,
      "adapter_fault": null
    },
    {
      "sample": 119,
      "version": 99,
      "cells": 2079,
      "adapter_fault": null
    }
  ],
  "longest_observed_map_version_stall_seconds": 17,
  "retained_map_version": 99,
  "retained_cells": 2079,
  "map_epoch_retained": true,
  "map_adapter_faults": [],
  "cleanup_verified": true,
  "owner_closed": true,
  "queue_depth": 0,
  "readers_joined": true,
  "resources_closed": true,
  "protected_runtime_preserved": true,
  "motor_commands_sent": false,
  "physical_autonomy_qualified": false,
  "goal_complete": false,
  "native_GNSS_factor_installed": false,
  "physical_motion_validated": false
}
```

현재 자동제어 supervisor의 모터 lease·arm·drive·Stop 코드는 설치되어 있지만, 실제 control_provider/evidence_provider 연결과 검증된 차체 방향·속도·제동 입력이 없어 기본 OFF다. GPS native 입력·원본 pose 이력·통합 occupancy 반영, 전체 10월 3일 지도 정확도, 9월 10일 과수원·가지·벽·마지막 열 검증은 미완료다. 실내 GPS NoFix를 RTK 위치로 처리하지 않는다. 휠 엔코더·PWM·카메라·시뮬레이션 truth를 위치 추정 근거로 대체하지 않는다.

재개 자료는 private `native-navigation-20261007/current-live-input-recovery-run.txt`와 해당 실행 폴더의 원본 빌드·설치·120초 시험·종료·테스트·Git 증거에 있다. 이전 실패와 지도를 삭제하지 않았다. 시뮬레이션 런타임은 이번 수정에서 변경하지 않았다.
