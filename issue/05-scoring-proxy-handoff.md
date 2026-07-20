# Scoring Proxy Handoff

이 문서는 다음 작업자가 현재 solver의 scoring proxy 문제를 이해하고 개선할 수 있도록 작성한 인수인계다. 핵심은 cyclex 자체가 아니라, 여러 의사결정층에서 쓰는 cheap proxy가 실제 Phase2 실현 결과, 특히 Z1/tardiness를 충분히 대변하지 못한다는 점이다.

## 한 줄 요약

현재 solver는 많은 결정을 `Z2 + Z3 + static crowd`류의 cheap proxy로 내린다. 하지만 실제 objective는 대부분 Phase2에서 드러나는 `Z1`이 지배한다. 이 proxy-reality gap 때문에 cyclex, repair, 초기 배정, 후보 확장 모두에서 "좋아 보이는 선택이 실제로는 지연을 키우는" 문제가 발생한다.

## 왜 이 문서가 필요한가

최근 cyclex 실험에서 proxy 문제가 매우 선명하게 드러났다.

`cyclex4` 첫 wave 결과:

```text
prob_26  proxy hit/miss = 0/4    real_best = +126,955
prob_28  proxy hit/miss = 0/10   real_best = +188,186
prob_31  proxy hit/miss = 0/6    real_best = +490,188
prob_30  proxy hit/miss = 0/7    real_best = +459,560
```

합계:

```text
proxy hit 0
proxy miss 27
```

즉 proxy는 후보를 "좋다"고 골랐지만, 실제 `realize(...)` 후에는 전부 `s_best`보다 나빴다. 이것은 cyclex만의 문제가 아니라, 현재 cheap scoring이 실제 배치 엔진의 지연 메커니즘을 못 보고 있다는 신호다.

## 현재 proxy가 쓰이는 곳

### 1. Phase1 initial bay assignment

파일:

```text
Phase1/greedy.py
```

역할:

```text
각 block을 어느 bay에 넣을지 결정
```

대략적인 scoring:

```text
score(i, bay) = w2 * Z2_load_proxy
              + w3 * preference_loss
              + crowd_penalty
```

문제:

- Phase1은 실제 placement 실패, crane obstruction, fragmentation을 모른다.
- 따라서 Z1이 나중에 어떻게 터질지 직접 보지 못한다.

### 2. ALNS repair

파일:

```text
Outer/repair.py
```

역할:

```text
destroy로 제거된 block을 다시 bay에 삽입
```

repair 역시 기본적으로 다음 계열을 본다.

```text
Z2 load delta
+ preference loss
+ crowd penalty
+ noise
```

문제:

- 한 block씩 재삽입하기 때문에 맞교환/연쇄 이동으로만 열리는 basin에 약하다.
- crowd proxy가 실제 dispatch 지연과 맞지 않으면 ALNS가 같은 근방만 돈다.

### 3. cyclex candidate scoring

파일:

```text
Outer/cyclex.py
```

역할:

```text
bay assignment vector에 대한 2-cycle/3-cycle 구조적 교환 후보 생성
```

현재 proxy:

```text
proxy_delta = w2 * delta_Z2_load_imbalance
            + delta_preference_loss
            + delta_static_crowd
```

문제:

- 현재 가장 명확한 proxy miss 관측 지점이다.
- 그러나 cyclex는 증상이지 원인은 아니다.
- cyclex가 후보를 더 많이 만들수록 proxy가 틀린 후보를 더 적극적으로 고르는 문제가 커진다.

### 4. Phase2 dispatch/raster 내부 scoring

파일:

```text
Phase2/dispatch.py
Phase2/raster.py
```

역할:

```text
실제 anchor/order/placement 결정
```

여기에는 contact, fragdelta, nestle, admission fail-stop 등 더 실제 배치에 가까운 로직이 있다. 하지만 상위 계층 proxy는 이 정보를 충분히 요약해서 쓰지 못한다.

## 현재 proxy의 본질적 한계

### 1. Z1을 직접 예측하지 않는다

목적함수는 대부분 다음 항이 지배한다.

```text
w1 * Z1
```

그런데 많은 proxy는 다음을 본다.

```text
Z2 load balance
Z3 preference loss
static area-time crowd
```

Z2/Z3는 중요한 부항이지만, 큰 문제에서는 Z1에 비하면 잔돈에 가까운 경우가 많다. 따라서 proxy에서 작은 이득처럼 보이는 선택이 실제로는 Z1을 크게 망칠 수 있다.

### 2. static crowd가 actual placement pressure와 다르다

현재 crowd 계열 proxy는 대체로 다음 식이다.

```text
EST 기반 시간창에서 bay area peak가 capacity 비율을 넘는가
```

하지만 실제 placement 가능성은 단순 면적합이 아니라 다음에 좌우된다.

- block shape
- orientation
- anchor availability
- residual fragment thickness
- crane sweep obstruction
- already placed blocks의 모양
- dispatch queue order
- dynamic bay reroute side effect

즉 `area-time crowd`는 너무 거칠다.

### 3. victim risk를 거의 보지 않는다

어떤 block A를 bay j로 옮기면, A만 좋아지는지 보면 안 된다. bay j에 있던 기존 block들이 밀릴 수 있다.

현재 proxy는 moved block 중심이다. 그러나 실제 Z1은 victim cascade에서 터질 수 있다.

필요한 질문:

```text
A가 bay j로 들어오면
  같은 시간대의 어떤 block K가 anchor를 잃는가?
  K의 due slack은 얼마인가?
  K가 하루 밀리면 w1 손실이 얼마인가?
```

이 victim risk가 빠져 있어 proxy miss가 난다.

### 4. forced 진단값과 proxy가 분리되어 있다

현재 forced 관련 값은 코드에 남아 있다.

- `Phase2/dispatch.py`
  - `forced_cons`: construction 중 배치되지 못해 뒤로 밀리는 block 집합
- `Phase2/driver.py`
  - repair 단계에서 `shift_later(...)`로 밀린 victim을 `forced`에 기록
- `Outer/realize.py`
  - `Solution.forced = len(res.info.get("forced", []))`

하지만 forced는 `realize(...)` 후에야 알 수 있는 결과값이다. 그래서 현재 cheap proxy에는 들어가지 않는다.

중요한 해석:

```text
forced가 사라진 것이 아니다.
forced는 결과 진단값으로 남아 있지만, 현재 proxy feature로 쓰이지 않는다.
```

또한 최근 병목은 단순히 forced 수만 줄인다고 해결되는 구조가 아니다. 38 bay0 귀속 분석에서 라우팅/변위 채널은 NO_ANCHOR 지배로 사실상 사망했고, 문제는 전역 배정 basin과 실제 placement 지형으로 옮겨갔다.

## 현재 관측된 부작용

### cyclex에서의 부작용

구버전 cyclex는 일부 문제에서 효과가 있었다.

```text
prob_28  -159,375
prob_35  -215,663
prob_39  -647,188
```

하지만 후보군을 확장한 cyclex4에서는 proxy miss가 폭발했다.

```text
prob_28: proxy miss 10/10, real_best +188,186
```

해석:

- cyclex 자체에는 유효 신호가 있다.
- 그러나 proxy가 약한 상태에서 후보 공간을 넓히면 더 나쁜 후보를 더 잘 찾게 된다.
- 따라서 문제는 cyclex operator보다 scoring proxy의 신뢰성이다.

### repair/ALNS에서의 예상 부작용

repair도 비슷한 proxy를 쓰므로 다음 문제가 가능하다.

```text
ALNS가 자주 방문하는 후보는 proxy상 좋아 보이는 후보
하지만 실제 realize 결과는 Z1 악화
결과적으로 near-miss가 반복되고 좋은 basin에 못 들어감
```

이 현상은 28/38의 randbay 실측과도 맞는다. 무작위 재배정은 좋은 basin을 찾았지만, 현재 guided search는 그 basin에 잘 도달하지 못했다.

## 개선 방향

### 1. proxy를 바로 바꾸기 전에 label을 늘린다

우선 cheap proxy와 real outcome의 차이를 더 잘 기록해야 한다.

현재 cyclex에는 일부 계측이 있다.

```text
cyclex_proxy_hit
cyclex_proxy_miss
cyclex_proxy_best
cyclex_real_best
cyclex_nodes_sum
cyclex_checked_sum
```

추가 권장 label:

```text
delta_Z1
delta_Z2
delta_Z3
delta_forced
delta_forced_construction
move_size
moved_bay_multiset
source_bays
target_bays
```

이 값들은 proxy 본체가 아니라, proxy 개선을 위한 training/diagnostic label이다.

### 2. late-window pressure를 추가한다

단순 crowd peak보다 due slack을 반영해야 한다.

예상 형태:

```text
pressure(i -> bay j) =
  sum over k in bay j overlapping with i:
      overlap(i, k)
    * area_interference(i, k)
    * urgency(k)
```

여기서:

```text
urgency(k) = w1 / max(1, due[k] - current_entry[k])
```

의미:

- 늦기 쉬운 block이 많은 시간대에 새 block을 넣는 이동은 위험하다.
- 단순 면적 과밀보다 Z1에 더 가까운 proxy다.

### 3. victim risk를 별도 항으로 둔다

moved block의 이득만 보면 안 된다. target bay의 기존 block이 얼마나 손해 보는지를 봐야 한다.

후보 항:

```text
victim_risk(i -> j) =
  sum over k in target bay j:
      time_overlap(i, k)
    * urgency(k)
    * shape_or_area_conflict(i, k)
```

간단한 1차는 shape conflict 없이 area/overlap만 써도 된다.

### 4. static EST 대신 realized timing 기반 feature를 우선한다

현재 solution `s`에는 이미 다음이 있다.

```text
s.entry
s.exit_
s.coords
s.orient
s.bay
```

proxy가 `pre.EST`만 보지 말고, 가능한 한 현재 realized timing을 써야 한다.

예:

```text
현재: est[i] = pre.EST[i]
개선: entry[i] = s.entry[i], exit[i] = s.exit_[i]
```

이것만으로도 실제 지연 상태와 더 가까워진다.

### 5. top-1 proxy 선택을 버리고 top-k real check를 고려한다

현재 cyclex는 proxy가 고른 best 1개만 realize한다. proxy가 불안정하면 top1이 계속 틀린다.

개선안:

```text
proxy top K 후보 저장
K=3~8
각 후보를 realize
실제 best improvement만 채택
```

비용은 늘지만, proxy가 ranking 전체를 틀리는지 top1만 틀리는지 구분된다.

### 6. 후보 확장과 proxy 개선을 분리한다

현재 cyclex4의 실패는 "후보 확장"과 "proxy 품질"이 섞여 있다.

권장 knob:

```text
candidate_mode = cost | expanded
```

- `cost`: 구버전. `w1*tard + w3*pref + area` 상위만 사용
- `expanded`: tardy seed + pref + overlap + Shaw neighbor

28은 구버전에서 개선됐고 expanded에서 실패했으므로, 이 둘은 반드시 분리해서 비교해야 한다.

## in-bay perturbation과의 관계

사용자 가설:

```text
다른 bay끼리만 교환하는 것이 병목일 수 있다.
같은 bay 내부 perturbation도 필요할 수 있다.
```

이 가설은 타당하다. 다만 이것은 scoring proxy 개선과는 다른 층이다.

현재 cyclex/repair proxy는 bay vector를 바꾸는 계층이다. 같은 bay 내부 순서나 anchor 선택을 바꾸려면 다음 중 하나가 필요하다.

```text
realize(...)에 order override 추가
Phase2/dispatch.py queue ordering hook 추가
Phase2/raster.py order_cells bias 추가
```

즉 in-bay perturbation은 Phase2 operator 문제다. proxy 개선 작업과 섞으면 원인 분석이 어려워지므로 별도 작업으로 분리하는 것이 좋다.

## 권장 작업 순서

1. proxy label 확장
   - `delta_Z1/Z2/Z3/forced` 기록
   - candidate move 구조 기록

2. candidate mode 분리
   - 구버전 `cost` mode 복구
   - 현재 `expanded` mode와 A/B

3. realized timing 기반 pressure로 교체
   - `pre.EST` 중심에서 `s.entry/s.exit_` 중심으로 이동

4. victim risk 추가
   - target bay의 overlap/urgency block 벌점

5. top-k candidate realize
   - proxy top1 실패 문제 분리

6. 그래도 38이 안 열리면 proxy 문제가 아니라 operator class 문제로 보고 in-bay perturbation 또는 GRASP-RCL로 넘긴다.

## 최소 성공 기준

proxy 개선의 첫 관문은 38이 아니다. 먼저 28을 복구해야 한다.

이유:

```text
구버전 cyclex는 prob_28에서 개선 성공
확장 후보군 cyclex4는 prob_28에서 proxy miss 10/10
```

따라서 최소 sanity gate:

```text
prob_28 T=600에서 baseline 대비 개선 또는 최소한 proxy hit 발생
proxy hit rate가 0이 아니어야 함
```

그 다음 35/39, 마지막으로 38을 본다.

## 주의사항

- best-only 채택은 유지할 것.
  - best가 아닌 후보를 SA accept로 현재 state에 넣으면 탐색 경로가 오염된다.
- proxy 개선 전 후보 공간을 무작정 넓히지 말 것.
  - cyclex4의 `px=0/27`이 경고다.
- forced는 cheap proxy feature가 아니라 우선 post-realize label로 쓸 것.
- cyclex는 증상 관측기다. 실제 과제는 scoring proxy가 Phase2/Z1을 더 잘 대변하게 만드는 것이다.