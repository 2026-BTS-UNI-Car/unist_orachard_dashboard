# 지도 셀 인코딩 소스 개선 · 2026-10-09

선택되지 않을 RLE 또는 packed 인코딩 생성을 생략했다. 기존 전송 바이트,
4096셀·4상태·행 순서·동일 길이일 때 RLE 선택 규칙은 유지한다. 센서,
지도 좌표·signed log odds·해상도·위치 추정·모터 제어는 변경하지 않는다.

Linux 원본 비교917개, 길이 경계3개, 실제 full/delta 패킷24개가 동일했다.
실제 타일96개 encode-only batch 중앙값은 10.194526→3.912987ms였다.
공개 소스 회귀4개도 통과했다. 이 값은 전체 실시간 지연이나 M12 결과가 아니다.

**현재는 소스 반영이며 Jetson 실행 이미지에는 아직 배포하지 않았다.**
Jetson SSH 읽기 확인에서 기존 WEB `f3d9e2a5…`, 470-scan 비활성 지도,
대기열0·조종 해제·자동 출력false 상태를 확인했다. GPS USB 통신은 정상이나
현재 위성0/no-fix다. 새 WEB 이미지의 ARM 검증·배포·복원은 별도 체크포인트다.

검증 근거: private `native-navigation-20261007`의
`lazy-tile-encoder-linux-7f6b3e696ca0495eb8873aacab20be68/RESULT.json`
SHA `d3411f580e683fa3ef6df9833b0b59e1c1e185db3c8ae43249ca16cf487673be`,
독립 검토 `tile-encoder-lazy-independent-review-20261009/REVIEW.json`
SHA `af349be18acf29cc0e9e117e0d4a1a78cfd207c9d274b53796469213a741aea4`.
