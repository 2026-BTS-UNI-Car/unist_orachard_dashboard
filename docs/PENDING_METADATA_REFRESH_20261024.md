# 영상 교체 중 추가 목록 갱신의 재생 유지

새 영상 URL을 적용한 뒤 metadata가 도착하기 전에 같은 URL의 기록 정보가 다시 갱신되면, 마지막 metadata handler가 재생 복구 의도를 잃었다. 재생 상태는 켜져 있지만 영상은 pause된 상태로 남을 수 있었다.

동일 URL에서도 `sourceSwitchTimeS`가 남아 있으면 기존 복구 의도를 유지한다. 저장된 cursor는 새 URL을 처음 적용할 때만 정하며, 중간의 명시적 seek와 Pause가 계속 우선한다. player를 다시 만들지 않는다.

실제 제품 함수를 실행하는 기존 집중시험에 추가 refresh·중간 seek·중간 Pause의 세 사례를 넣었다. 수정 전 첫 사례가 실패했고, 수정 뒤 전체 native-video 회귀와 관련 집중시험 다섯 개 및 app 구문 검사가 통과했다.

이 후보는 첫 릴리스 `edfac41a8bea1fb5501e34702f4bda7ae4b78000`에서 별도로 준비했다. 실제 브라우저·Jetson 검증과 설치는 하지 않았다. 수동 제어·STOP·기록 원본·서버 및 지도 처리는 수정하지 않는다.
