# 설계 변천 ④ — 변경 4: 같은 시간에 더 많이 (탐색 가속·다양성) + 현재 상태

> **설계 변천 5부작** — [1] 전체 흐름 · 밀도 회수 · [2] 배정을 Z1-aware하게 · [3] 배정 고정 전제 깨기 · **[4] 탐색 가속·다양성** · [5] 탐색·배포 정밀화·인프라 정리
>
> 전체 흐름·문제의식·로드맵은 [1부](EVOLUTION1.md) 참고.

> ▸ **[1–3부]로** 밀도 회수·Z1-aware 배정·배치 시점 동적 재배정을 갖췄다. 이 셋은 전부 배치
> 엔진과 ALNS의 **매 반복에 얹혀** 탐색 1회를 무겁게 만든다. 이번엔 1회를 싸게 만들고 서로
> 다른 전략을 병렬로 돌린다. 마지막에 현재 pipeline의 파라미터 전달 지도를 정리한다.

---

## 사고 (로드맵 계획 4)

> **같은 시간에 더 많이 시도하자.** 위 세 개를 켜면 탐색 1회가 무거워진다. 1회를 싸게 만들고,
> 서로 다른 전략을 병렬로 돌려 best만 취한다. → *Phase2 + Outer.*

ALNS는 매 반복 pipeline 전체를 다시 돌리므로(`realize`), 1회를 싸게 만들면 같은 시간에 더 깊이·
더 넓게 탐색한다. 단, 순수 가속만으로는 같은 궤적을 더 깊이 팔 뿐 목적함수를 크게 못 움직인다
— 그래서 **서로 다른 전략을 병렬로 돌려 best만 취하는** 쪽에 무게를 둔다.

---

## 변경 4 — 탐색 1회를 싸게 + 서로 다른 전략을 병렬로

**어디를 바꿨나:** `Phase2/raster.py`(scan 비용) + `Phase2/dispatch.py`(꼬리 절단) +
`Outer/portfolio.py`(다양성).

**바꾼 것:**
- **국소 재계산 scan** ([raster.py:133-212](../Phase2/raster.py#L133)): block을 하나 넣거나 뺀 뒤,
  bay 전체가 아니라 **바뀐 자리 주변 sub-window만** 다시 scan한다(전체 재계산과 bit 단위 동일).
  → 벽시계 −20~35%.
- **mask 공유 캐시** ([raster.py:31-36](../Phase2/raster.py#L31)): (block, orientation) → convex-hull
  mask는 한 번 만들면 불변이므로, 전처리 번들(pre)에 붙여 모든 배치 실행이 재사용한다.
  → 두 번째 이후 실현 −29~82%.
- **admission 꼬리 절단** ([dispatch.py:210-213](../Phase2/dispatch.py#L210)): 한 bay의 admission
  pass에서 마지막 성공 이후 연속 F번 실패하면 남은 queue를 다음 event로 넘긴다. scan 비용의
  76–81%가 "마지막 성공 이후의 실패 꼬리"에 소모되기 때문이다. 이건 궤적을 바꾸는 레버라, 특히
  탐색이 굶는 극혼잡 문제가 첫 반복들을 확보하게 돕는다.
- **포트폴리오 다양성** ([portfolio.py:32-41](../Outer/portfolio.py#L32)): 여러 worker를 서로 다른
  urgency 감쇠 κ와 꼬리 절단 F로 분기시키고 best만 취한다(min-wins). 안전한 floor 워커가 있어,
  공격적인 worker는 **어떤 문제에서 이기면 이득만 보태고 지면 무시**되는 구조적 무회귀가 된다.
  (현재의 구체적 워커 구성 — dyn on/off 페어 + 정체 재시작 — 은 [5부]에서 다룬다.)

**얻은 것:** 같은 예산에서 탐색 깊이·다양성 확보. (가속 자체는 목적함수 중립이지만, 다양성
min-wins가 문제별 최적 전략을 포획한다.)

---

## 현재 상태 — 파라미터 전달 지도

네 변경이 끝난 지금 pipeline은 모든 층에서 Z1을 의식한다.

| 층 | Z1을 의식하는 방식 | 관련 knob (전달 경로) | 부(部) |
|---|---|---|---|
| Phase0 | convex-hull mask superset로 밀도·soundness 확보 | — (geometry table) | ① |
| Phase1 배정 | 혼잡 페널티를 w1 스케일로 부과 | `Phase1Config.crowd_weight=6.0` → `greedy._crowd` | ② |
| Phase2 배치 | urgency(ATC) 시간순 + tardiness 시 bay 덮어쓰기 | `atc_kappa`, `dispatch_dynamic_bay` → 배치 엔진 | ③ |
| Phase2 기하 | 팽창 제거로 밀도 회수, 정확 gate로 crane 보장 | `scan_incremental`, `mask_cache_share` | ①④ |
| Outer 재배정 | repair에 동일 혼잡 페널티(both-mode) | `OuterConfig.crowd_weight=6.0`, `crowd_eta=0.85` | ② |
| Outer 탐색 | κ·F 다양성 병렬 worker, best-of-N | `default_portfolio()` | ④ |

**전체 데이터 흐름과 파라미터 전달:**

```
myalgorithm.algorithm(prob_info, timelimit)
  → optimize_portfolio(...)                         # 4 worker 병렬 subprocess
      각 worker = default_portfolio()[i] = OuterConfig(
          xi, seed,
          phase2 = Phase2Config(atc_kappa=κ_i, dispatch_admit_fail_stop=F_i))
      → alns(prob_info, pre, cfg)                    # bay 배정 공간 탐색
          → BuildBayAssignment(prob_info, pre, cfg.phase1)   # 초기 배정
              firstfit_greedy: score = w2·Z2 + w3·pref + cw·w1·peak/WH
          → 반복: destroy → repair(cw·w1 혼잡) → realize → accept(SA)
              → realize(bay, prob_info, pre, cfg.phase2)
                  → init_timing (ENTRY=EST, EXIT=EST+P, Z1/Z2/Z3)
                  → PlaceAndCrane(prob_info, p1, pre, cfg)
                      → 시간순 배치 엔진(dispatch_construct)  # 배치 + dynamic bay
                          returns (coords, orient, entry, exit_, forced, BAY')
                      → BAY'로 bay/bay_blocks 갱신      # 재라우팅 반영
                      → crane 인증(공식 utils) → Phase A(하루씩 미룸)
                                              → Phase B(슬롯 재시도 → 빈 창 강제)
                  → 공식 check_feasibility로 목적함수 인증
                  → Solution.bay = 실현된 BAY'         # ALNS로 되먹임
      → 모든 worker 결과 중 best 반환 (min-wins)
```

**요지:** 목적함수를 지배하는 것은 Z1이고, Z1은 배치 시점에야 실체가 된다는 한 가지 관찰에서
출발해 — 잔돈(Z2·Z3)이 배치의 뼈대를 선점하던 구조를 위에서 아래로 뒤집었다. 공간을 버리지
않게 하고([1부]), 가장 앞 배정이 미래 혼잡을 보게 하고([2부]), 배정 고정이라는 전제 자체를 배치
엔진이 깰 수 있게 하고([3부]), 그 모든 것을 병렬로 더 많이 시도하도록([4부]) 만든 것이
perf_out_dispatch에서 여기까지의 변천이다.

---

**이전 ←** [3부] 배정 고정이라는 전제 자체를 배치 엔진이 깨기 (동적 bay)
**다음 →** [5부] 탐색·배포 정밀화와 인프라 정리.
