# OGC 2026 성능/시간제한 분석 보고서

- 시작 2026-07-11 17:44:53 · 갱신 2026-07-11 17:46:15 · 진행 **4/20** (anytime 4 · real 0 · 오류 0)
- 설정: mode=anytime · anytime horizon=5s(member0 단일 ALNS, 시계=load+preprocess+alns, 진단 제외) · real timelimits=[30.0, 60.0] grace=0.15 · scoring_profile=baseline · 체크포인트=['5', '10', '20', '30', '40', '50', '60', '90', '120', '150', '180']

> 채점 계약(§3.2~3.3): timelimit 문제별 상이(수분~30분), **시간초과=crash=infeasible=−1점**, 자원 4코어/16GB. 본 수치는 개발 머신 기준 → 절대 초가 아니라 **여유 비율(margin_frac)** 로 판단.

## 1. 진단 요약 (anytime 성공 문제 기준)

| 지표 | 평균 | 중앙값 | 최소 | 최대 |
|---|---|---|---|---|
| Phase0 preprocess (s) | 0.010 | 0.009 | 0.009 | 0.011 |
| Phase1 assignment (s) | 0.024 | 0.022 | 0.021 | 0.031 |
| Phase1 timing (s) | 0.000 | 0.000 | 0.000 | 0.001 |
| Phase2 first place cold NFP (s) | 4.820 | 4.874 | 3.328 | 6.202 |
| Construction startup total (s) | 4.854 | 4.910 | 3.362 | 6.234 |
| 첫 인증해 시각 t_first_solution (s) | NA | NA | NA | NA |
| ALNS iters/sec | 0.00 | 0.00 | 0.00 | 0.00 |
| 반복 1회 비용 p90 (ms) | NA | NA | NA | NA |
| 반복 1회 비용 max (ms) | NA | NA | NA | NA |
| ALNS improve @5s (%) | NA | NA | NA | NA |
| forced (ALNS best) | 21.8 | 21.5 | 18 | 26 |

## 2. 가상 timelimit 별 품질 (한 번의 긴 관측에서 후처리로 읽음)

셀 = 그 시각의 incumbent objective (최종 대비 +gap%). `-1` = 그 시각까지 인증해 없음(서버라면 **−1점**).

| prob | h(s) | 5s |
|---|---|---|
| train/prob_1 | 5 | **-1** |
| train/prob_2 | 5 | **-1** |
| train/prob_3 | 5 | **-1** |
| train/prob_4 | 5 | **-1** |

> **4개**는 gap 집계·곡선에서 제외(h=5s 절단 시 미포화이거나 관측이 그보다 짧음).

### 2.1 수렴/포화 ("몇 초면 충분한가")

| prob | 첫 해(s) | 최종1%이내 도달(s) | 마지막 개선(s) | 개선횟수 | 포화@h |
|---|---|---|---|---|---|
| train/prob_1 | NA | NA | NA | 0 | N(h=5s 부족) |
| train/prob_2 | NA | NA | NA | 0 | N(h=5s 부족) |
| train/prob_3 | NA | NA | NA | 0 | N(h=5s 부족) |
| train/prob_4 | NA | NA | NA | 0 | N(h=5s 부족) |

> **4개 문제가 관측 지평 끝까지 개선 중** — 더 긴 --horizon 재관측 권장.

## 3. 구성(startup) 시간의 phase별 비중

- Phase0 preprocess : **  0.2%** (0.0s)
- Phase1 배정+타이밍 : **  0.5%** (0.1s)
- Phase2 첫배치      : ** 99.3%** (19.3s)  ← lazy NFP 빌드(문제당 1회)

> 반복 1회 비용은 cold가 아니라 **iter ms**(warm). 해 품질 ∝ **iters/sec**이므로 낮은 문제가 실질 병목.

## 4. 병목 함수 (realize self-time 비중, 전 문제 평균)

| 함수 | 평균 비중 (%) |
|---|---|
| `_monotone_chain (geometry.py:252)` | 8.6 |
| `_seg_overlap_len (geometry_query.py:198)` | 8.0 |
| `union_all (set_operations.py:463)` | 6.2 |
| `_in_ring_py (geometry_query.py:68)` | 4.6 |
| `collision_oracle (collision.py:11)` | 4.3 |
| `shared_edge_length (geometry_query.py:261)` | 4.2 |
| `_vertex_candidates (candidates.py:39)` | 4.0 |
| `wrapped (decorators.py:73)` | 3.2 |
| `_collision_free (collision.py:29)` | 2.6 |
| `cross (geometry.py:258)` | 2.6 |
| `<built-in method builtins.len> (~:0)` | 2.3 |
| `intersection (set_operations.py:112)` | 1.9 |

> realize hot loop CPU의 비중(%). 상위 함수 최적화 -> iters/sec 상승.

## 6. 시간제한 정책 산정 근거 (실측)

- 첫 인증해 최악 시각: **0.0s** (이보다 짧은 timelimit이면 그 문제는 −1 확정)
- 반복 1회 최악 비용: **0.0s** (realize는 variant 도중 중단 불가 → 딜라인 초과분의 하한)

> 딜라인은 고정 상수가 아니라 `timelimit - reserve`로 인자에서 유도할 것. reserve 크기는 위 실측치(첫 해 시각·반복 최악 비용)와 real 모드의 여유 비율로 산정.

## 7. 상관

- 처리량 최저 top-3 (n_blocks, iters/s): (100, 0.00), (100, 0.00), (100, 0.00)
- forced(강제배치) ↔ Z1(지연) 상관계수: **+0.97** (+1에 가까울수록 강제배치 줄이기가 Z1 최우선 레버)

## 8. 문제별 상세 (anytime)

| prob | n_blk | P0(s) | P1(s) | P2cold(s) | ALNS(s) | iters | iters/s | improve% | 포화 | forced | Z1 | objective | feas |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| train/prob_1 | 100 | 0.009 | 0.021 | 5.557 | 5.64 | 0 | 0.00 | 0.00 | N | 21 | 1400 | 40742803 | Y |
| train/prob_2 | 100 | 0.011 | 0.023 | 3.328 | 5.51 | 0 | 0.00 | 0.00 | N | 18 | 974 | 28340574 | Y |
| train/prob_3 | 100 | 0.010 | 0.032 | 4.192 | 5.60 | 0 | 0.00 | 0.00 | N | 22 | 1386 | 37025402 | Y |
| train/prob_4 | 100 | 0.009 | 0.023 | 6.202 | 5.73 | 0 | 0.00 | 0.00 | N | 26 | 2357 | 51744667 | Y |
