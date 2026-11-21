# 격자 지도 확인 도구 (`tools/map_check/`)

격자 지도의 위치 추정기를 기록에 다시 돌려, 고치기 전과 뒤의 코드를 같은 기록으로 견주는 도구다. 측량 기준 없이 볼 수 있는 것만 본다.
방법과 2026-11-21에 잰 값은 [`docs/GRID_MAP_KO.md`](../../docs/GRID_MAP_KO.md)에 있다.

| 파일 | 하는 일 |
|---|---|
| `run_localizer.py` | 기록 하나의 센서 파일(`mapping_source/sensors.jsonl.gz`)을 기록 재생과 똑같은 순서로 추정기에 넣고, 스캔마다의 위치·맞춘 방식·잔차·IMU 방향·처리 시간과 빔 여덟 중 하나를 `.npz` 로 남긴다 |
| `consistency.py` | 그 결과에서, 스캔의 점이 4~12 s 앞선 스캔들의 점 위에 얼마나 잘 놓이는지와 모든 점이 차지한 5 cm 칸 수를 낸다 |
| `gnss_fit.py` | 추정한 길을 회전과 평행 이동만으로 그 기록의 GNSS 궤적에 겹쳤을 때 남는 거리를 낸다. 방향을 IMU 로만 했을 때의 값도 같이 낸다 |

## 쓰는 법

```bash
python -B tools/map_check/run_localizer.py <기록 폴더> <결과.npz>
python -B tools/map_check/consistency.py <결과.npz> [<다른 판의 결과.npz> ...]
python -B tools/map_check/gnss_fit.py <기록 폴더> <결과.npz> [<다른 판의 결과.npz> ...]
```

- 기록 폴더에는 `metadata.json` 과 `mapping_source/sensors.jsonl.gz` 가 있어야 한다. 기록은 읽기만 한다. 결과는 레포 밖에 둔다.
- 고치기 전의 코드로 돌릴 때는 그 커밋을 다른 폴더에 꺼내(`git worktree add`) 그 폴더의 `tools/map_check/run_localizer.py` 를 쓴다.
  (이 도구가 생기기 전의 커밋에는 도구 파일만 복사해 넣어 돌린다. 2026-11-21 전의 코드에는 IMU 방향 값이 없어 그 칸은 비어 나온다.)
- 처리 시간은 그 PC 의 값이다. 견줄 때는 두 판을 같은 PC 에서 다른 일 없이 차례로 돌린다.

## 알아 둘 것

- **GNSS 좌표를 쓰지 않는다.** GNSS 는 그 기록의 첫 유효 위치에서의 동·북 거리(m)로만 다루고, 위도·경도는 출력하지 않는다.
- 세 수치 모두 참값과의 차가 아니다. `consistency.py` 는 가까운 시간 안의 일관성을, `gnss_fit.py` 는 GNSS 정확도(단독 측위면 수 m)보다 큰 모양 차이를 본다.
- 자율주행 기록이면 실행기가 잰 "열 대비 방향"을 더해 열 방향이 일정한지 볼 수 있다(문서 5절의 방법). 그 계산은 녹화의 bag 을 읽어야 해서 여기에는 없다
  (시뮬레이션 저장소 `tools/field_records/`).
