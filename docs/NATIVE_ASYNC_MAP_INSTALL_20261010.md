# Native 지도 관찰 기능: 실제 설치 및 제한

요청한 2시간 기한을 지키지 못했습니다. 실차 자율주행은 완료되지 않았습니다. 이번에 확인한 것은 원본 LiDAR·IMU 기반 진단, 역사 지도 생성·전송, 명시적 Stop과 정리입니다. 모터·자동주행 출력은 계속 0이고 모든 실행 권한은 꺼져 있습니다. 바퀴 전원 OFF는 사용자 보고이며 독립적인 물리 차단 검증은 아닙니다.

V15 native 서비스와 WEB 지도 경로를 실제 설치했습니다. 비동기 지도 worker, 연속된 원본 센서 쌍의 SE2 연결, 동일 끝점 광선 중복 제거, 기존 타일 codec을 이용한 페이지 전송을 결합했습니다. ROOT가 실행한 기본 스트림 테스트 140개와 별도의 결합 소스 테스트 Python 55개·Node 12개가 통과했습니다. 서로 겹치는 suite의 숫자는 고유 테스트 총수로 합산하지 않습니다. 이 소스 테스트는 실차 지속 동작이나 물리 정확도 인증이 아닙니다.

실제 R14 관찰에서 진단 65개와 전송 가능한 지도 버전 21, 역사 셀 2,055개를 확인했습니다. 완료 지도 API 표본은 5회이며 버전은 `15, 21, 21, 21, 21`입니다. 이것은 새 지도 5개가 생성됐다는 뜻이 아닙니다. 같은 map epoch를 유지했고 Stop 이후에도 마지막 완성 버전 21과 셀을 보존했습니다. 내부 map worker 버전과 전송이 완료된 버전은 구분합니다. ROOT는 실제 Jetson 대시보드를 열어 해당 지도가 Canvas에 표시되고 관찰 종료 후 마지막 지도 21·2,055셀이 유지되는 화면도 확인했습니다. 확대·이동 보존, 경로 표시, 재개·새 지도의 모든 동작을 검증한 것은 아닙니다.

원본 센서 처리는 `commit_initial_association` 단계에서 `stale_original_causal_imu`로 실패했습니다. 해당 실패 시각의 SOURCE/RAW 나이는 기록되지 않았습니다. 이전 WAIT의 나이나 마지막 성공 진단의 TTL 여유를 이번 실패의 측정값으로 사용할 수 없습니다. V16의 좁은 후속 수정은 준비 중이며 아직 이번 결과를 해결했다고 판단하지 않습니다.

종료된 API 시험의 `api_observation_exercise_ok`와 `cleanup_verified`는 참입니다. 그러나 `sustained_native_input_qualified`, `sustained_mapping_qualified`, `M12_qualified`, `physical_autonomy_qualified`는 모두 거짓입니다. 60회 조회를 유지한 동안 새로운 진단이 계속 나왔다는 뜻도 아닙니다. 종료 시 native owner 소유권 없음, reader join·자원 종료, 큐 깊이 0, 프로세스 exit 0, 강제 kill 없음, 지도 worker 종료와 페이지 lease 0을 확인했습니다. WEB 교체 후 재설정된 기준의 13개 서비스 프로필이 유지됐습니다.

WEB 교체의 앞선 두 실패는 원래 WEB으로 정확히 롤백했고 증거를 보존했습니다. 두 시도 당시 native 서비스는 `failed` 상태였고 준비 완료 조건을 충족하지 못했습니다. 후속 실행안은 자원 정리가 확인된 정상 `stopped`도 허용하지만 `failed`를 허용하지 않습니다. native V15를 새로 idle 설치한 뒤 세 번째 WEB 교체가 성공했습니다. 이전 WEB CID는 삭제하지 않고 중지 상태로 보존했습니다. 지도 GET은 센서 처리를 시작하지 않습니다.

현재 원본 지도 경로는 LiDAR·IMU만 사용하며 카메라·PWM·바퀴 오도메트리·truth·GPS를 위치추정 factor로 사용하지 않습니다. GNSS는 현재 Fix가 없고 native GPS factor도 연결되지 않았습니다. BODY 변환·장착 방향·제동·비상정지 실측, 줄 끝·다음 줄·마지막 줄 판단, 움직이는 실차의 지도 정확도와 실제 자율주행 검증은 남아 있습니다. 지속 입력 복구, 브라우저의 재개·새 지도 및 조작 검증, M12 source-to-map p95 검증도 남아 있습니다.

현재 설치 식별자는 다음과 같습니다. 이는 인증 정보나 출력 권한이 아닙니다.

- Native CID: `84e82423aed6cb5258ecb075160fd9499b44b7ee5e8ac0aeea03ac448352a2d4`
- Native image: `sha256:d2bdd6cd7ff1dc2c88cf9e64e2d7e62c5d8e0af6354f0ac74731c1dbc269729c`
- Native source SHA: `5cc852e3b74e666abaf0a38c9fd6599a0df0ae4f20ec82b84de815316a705e09`
- Package SHA: `4669f543c358ca563c854dbddd70ca889d64f1837e012f29cc4dbc4f5a87818e`
- WEB CID: `97ba8af5164fb770461e6ead22f586c07cf3a2680f44f7a267ba29f92b474872`
- WEB image: `sha256:e500f240838ae4e7b079a6fd96eb2a59e5f283f021fae5b0b2d1d928a14ac423`
- 보존된 이전 WEB CID: `3157789f6dc3ea61bb4e6ed75cd1773442ead6ddfb74b73fc0a595298420b9f3`

종료 증거는 `native-v15-map-root-20261010-1791586720778766600`의 `ACTUAL_API_R14_CORRECTED_PROBE.stdout.json`, `PROBE_FAILURE_DETAIL.json`, `FINAL_READONLY_RUNTIME_POST_WEB.stdout.json`, `WEB_OVERLAY_V3.stdout.json`입니다. 개별 증거 SHA와 설치본·Git 커밋 연결은 private 체크포인트에 보존합니다. 웹 환경 변수·원시 실행 프로필·소유권 토큰은 게시하지 않습니다.
