# 지도 셀 인코딩 소스 개선 · 2026-10-09

선택되지 않을 RLE 또는 packed 인코딩 생성을 생략했다. 기존 전송 바이트,
4096셀·4상태·행 순서·동일 길이일 때 RLE 선택 규칙은 유지한다. 센서,
지도 좌표·signed log odds·해상도·위치 추정·모터 제어는 변경하지 않는다.

Linux 원본 비교917개, 길이 경계3개, 실제 full/delta 패킷24개가 동일했다.
실제 타일96개 encode-only batch 중앙값은 10.194526→3.912987ms였다.
공개 소스 회귀4개도 통과했다. 이 값은 전체 실시간 지연이나 M12 결과가 아니다.

**Jetson WEB 이미지 `93a17de4…`에 배포했다.** 기존 이미지 기반으로 인코더
한 파일만 바꿨으며 ARM 전후4개씩 검증했다. 이전 WEB CID·이미지는 정지 상태로
남겨뒀다. 나머지12개 서비스의 전체 실행 설정, 470-scan 지도 바이트·epoch,
자동주행 설정과 기록·data·config 메타데이터가 유지됐다. 전체 기록 바이트를
재해시한 검증은 아니다. SSH와 Cloudflare health·지도 GET을 다시 확인했다.
지도는 비활성 `needs_reset`·대기열0, 조종 해제·자동 출력false다.
GPS USB 통신은 정상이나 확인 당시 위성0/no-fix다. 실제 rollback 실행,
전체 실시간 성능·위치 정확도·자율주행 합격은 별도 검증 대상이다.

배포 근거: `actual-lazy-encoder-ROOT-apply-f97ab8d630394f6680dfd1f3b74239d4/RESULT.json`
SHA `3aecc0207eab80dabdb098cdbdd789b4ee328cc17dc01c204a86529e89387fc2`.
재확인: `actual-lazy-encoder-POST-readonly-1791490734850699500/RESULT.json`
SHA `1056e4948193130ba35f78d2917643476b4e3125df31d6045d3ff4997baa1f97`.

검증 근거: private `native-navigation-20261007`의
`lazy-tile-encoder-linux-7f6b3e696ca0495eb8873aacab20be68/RESULT.json`
SHA `d3411f580e683fa3ef6df9833b0b59e1c1e185db3c8ae43249ca16cf487673be`,
독립 검토 `tile-encoder-lazy-independent-review-20261009/REVIEW.json`
SHA `af349be18acf29cc0e9e117e0d4a1a78cfd207c9d274b53796469213a741aea4`.
