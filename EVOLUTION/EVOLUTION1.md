# 설계 변천 ① — 전체 흐름 + 변경 1: 공간을 버리지 않기

> **설계 변천 5부작** — **[1] 전체 흐름 · 밀도 회수** · [2] 배정을 Z1-aware하게 · [3] 배정 고정 전제 깨기 · [4] 탐색 가속·다양성 · [5] 탐색·배포 정밀화·인프라 정리
>
> 큰 주제: *Z1이 목적함수를 지배하는데, Z2·Z3가 자리 배치를 선점하는 게 문제 아닐까?*
> 이 5부작은 이벤트 기반 시간순 배치 엔진(perf_out_dispatch 단계)을 확보한 뒤, 목적함수를
> 다시 읽는 데서 출발해 pipeline을 단계적으로 바꿔 현재에 도달한 사고 과정과 실제 코드
> 변경을 정리한다. 각 부는 "왜 그렇게 생각했나 → 어느 phase의 어떤 logic을 바꿨나 →
> 무엇을 얻었나" 순서로 쓴다.

---

## 한눈에 보는 전체 흐름 (직관)

이 그림을 머릿속에 두면 뒤따르는 내 변경이 각각 어느 지점을 건드리는지가 선명해진다.

**입력.** 문제는 두 덩어리로 들어온다.
- **bay** 여러 개: 각각 폭×높이(W×H)의 직사각형 작업장이고, **crane 한 대**가 딸려 있다.
  crane은 수직으로만 오르내리며 block을 넣고 뺀다.
- **block** 여러 개: 불규칙한 **다층(multi-layer) 폴리곤**(예: 아래층이 넓고 위층이 좁음).
  회전해서 놓을 수 있어 **orientation**이 여러 개다. 각 block은 **release time**(이때부터
  넣을 수 있음), **due date**(이때까지 빼야 벌점 없음), **processing time**(최소 체류 기간),
  **workload**(부하), **bay 선호도**를 갖는다.
- **목적함수 가중치** w1, w2, w3.

각 block은 "어느 bay에 · 어디에(x, y, 회전) · 언제 넣고(ENTRY) · 언제 빼는지(EXIT)"를
동시에 정해야 한다. 이걸 네 단계로 나눠 푼다.

### Phase0 — 기하 사전을 만든다 (아직 아무것도 안 놓음)

배치와 무관하게 한 번만 계산되는 것들:
- block별 상수: **EST**(= release, 가장 이른 시작), **slack**(= due − release − proc, 시간
  여유), 최대 선호값.
- bay별 면적 가중치 **u** (작은 bay일수록 부하가 무겁게 계산됨).
- (block, orientation)별: 단순화된 폴리곤, bounding box, **IFP** — "이 block을 이 bay 안에
  (밖으로 안 삐져나가게) 놓을 수 있는 정수 기준점 범위". IFP가 비면 그 bay엔 애초에 못 들어간다.
- **No-Fit-Polygon(NFP)**: 두 block이 겹치는지를 "상대 위치가 이 폴리곤 안이면 겹침"으로
  바꿔 놓은 표. crane 규칙(위층 j가 아래층 k의 진입을 막음, j ≥ k)까지 포함. 필요할 때만
  lazy하게 계산·캐시.

→ 요지: Phase0은 **"누가 어디에 들어갈 수 있고, 둘이 어떤 상대 위치에서 부딪히나"의 사전**만
만든다. 아직 아무 block도 배치하지 않는다.

### Phase1 — 어느 bay에? (좌표·시각은 아직 안 정함)

block마다 **들어갈 bay 하나만** 고른다. 마감이 급한 순(slack 작은 순, EDD)으로, 후보 bay 중
**점수가 가장 낮은** 곳에 넣는다:
- 점수 = w2·(부하 불균형 Z2 증가분) + w3·(선호 벌점 Z3) + **혼잡 페널티**(이 bay가 이 block
  머무는 동안 얼마나 붐빌지).
- 물리적으로 절대 안 들어가는 bay는 면적 하한(DFF)으로 미리 배제.

→ 이 단계에서 **Z2·Z3가 확정**된다(bay 배정만으로 결정되니까). tardiness(Z1)는 아직 모른다.
시각은 잠정값(ENTRY = release, EXIT = release + proc)만 잡는다.

### Phase2 — 정확히 어디에, 언제? (여기서 진짜 Z1이 나온다)

bay 배정을 고정으로 받아 **좌표·회전·ENTRY/EXIT 시각**을 **시간순**으로 정한다. 시간을
앞으로 흘려보내며, block이 풀리는(release) 시각과 나가는(exit) 시각마다:
1. 그 시각에 나가는 block을 빼고,
2. 그 시각에 풀린 block을 대기열에 넣고,
3. 대기 block을 **급한 순(urgency)** 으로 하나씩 자리 찾아 넣는다.
   - 급함 = ATC 점수: **곧 늦을 것 같고(slack 작음) 빨리 끝나는(proc 짧음)** block 먼저.
   - 자리 = bay 격자를 전수 훑어 빈 지점을 찾고, 이미 놓인 것과 **잘 맞물리는**(접촉 큰)
     자리부터, **crane이 넣고 뺄 수 있는지** 정확히 검사해 통과한 첫 자리.

→ 중점은 **tardiness 최소화**: 급한 block을 가능한 한 **이른 자리**에. 자기 bay에 못 넣는데
이미 늦는 급한 block이면 **다른 bay로 재라우팅**, 그래도 안 되면 마지막에 빈 구간으로 강제.
끝나면 공식 crane 체커로 인증하고, 충돌하면 하루씩 미루거나 다른 슬롯으로 고친다.

### Outer(ALNS) — 배정을 더 좋게 반복

Phase2가 낸 **진짜 Z1**을 보고, **bay 배정 자체를 바꿔가며** 개선한다:
- **destroy(부수기):** 현재 배정에서 block 몇 개를 뺀다 — 무작위로 / **목적함수에 크게
  기여하는(늦거나 선호 나쁜)** 것으로 / 서로 관련된(같은 bay·비슷한 시각·가까운 위치·비슷한
  형상) 무리로.
- **repair(복구):** 뺀 block들을 빠른 배정 비용(Z2 + 선호 + 혼잡)으로 다시 넣는다.
- **realize(평가):** 새 배정으로 Phase1 시각 + Phase2 배치·crane 전체를 **다시 돌려** 실제
  목적함수를 얻는다.
- **accept(수용):** 나아지면 받고, 나빠도 확률적으로 받아(담금질 SA) 국소최적을 벗어난다.
- 어떤 destroy/repair가 잘 먹히는지 성과로 가중치를 학습(AOS)하고, 냉각하며 시간 예산까지 반복.
- 이 전체를 **서로 다른 전략(급함 감쇠 κ, 꼬리 절단 F)의 worker 4개**로 병렬로 돌려 **가장
  좋은 결과만** 취한다.

**한 줄 요약:** Phase0(기하 사전) → Phase1(어느 bay) → Phase2(어디·언제, 여기서 Z1) →
Outer(배정 개선 반복).

---

## 0. 출발 상태 — perf_out_dispatch가 무엇이었나

이 시점의 solver는 이미 위 4단 pipeline을 갖고 있었다.

```
prob_info
  → Phase0 (preprocess): 상수(u, EST, slack) + geometry table(poly, bbox, IFP)
                          + lazy No-Fit-Polygon + convex-hull mask 재료
  → Phase1 (bay 배정):   각 block을 어느 bay에 넣을지 greedy로 고정 (Z2, Z3 확정)
  → Phase2 (배치):       block마다 (x, y, orientation)과 ENTRY/EXIT 시각을
                          시간순으로 결정 → 실제 Z1(tardiness) 확정
  → Outer (ALNS):        bay 배정을 바꿔가며 위 Phase1→2를 반복 평가, best 유지
```

핵심은 Phase2의 **시간순 배치 엔진**(`Phase2/dispatch.py`의 `dispatch_construct`)이었다.
이 엔진은 시간을 앞으로 흘려보내며(release 시각 ∪ 예정된 EXIT 시각을 event로) 매 순간마다:

1. 그 시각에 나가는 block을 bay에서 빼고,
2. 그 시각에 release된 block을 대기 queue에 넣고,
3. 대기 block을 **urgency 순서**로 하나씩 bay에 넣어본다.

urgency는 ATC(Apparent Tardiness Cost) 점수다([dispatch.py:77-82](../Phase2/dispatch.py#L77)):

```
priority(i, t) = 1 / (P_i) · exp( −max(0, slack_i(t)) / (κ · P̄) )
   slack_i(t) = due_i − P_i − t      (지금 넣으면 마감까지 남는 여유)
   P̄ = 평균 processing time
```

여유(slack)가 작을수록, 처리시간 P가 짧을수록 우선. 즉 "곧 늦을 것 같고 빨리 끝나는
block부터" 넣는다. 자리를 찾을 땐 bay 격자(raster)를 전수 scan해 빈 anchor를 구하고, 접촉
(맞물림) 점수가 높은 cell부터 crane 통과 여부를 정확히 검사한다. 끝까지 못 들어간 block은
마지막에 빈 window로 force-place해서 출력은 항상 완전한 feasible 배정이 된다.

이 재작성으로 force-place와 timeout이 사실상 0이 되었고 목적함수가 크게 떨어졌다.
**여기까지가 출발점이다.** 5부작은 이 위에서 무엇을 더 바꿨는가의 기록이다.

---

## 1. 문제의식 — 목적함수를 글자 그대로 다시 읽다

배치 엔진이 안정되고 나서, 개선 여지를 찾으려고 목적함수 정의로 돌아갔다.

$$ \text{minimize} \quad w_1 Z_1 + w_2 Z_2 + w_3 Z_3 $$

- **Z1 = tardiness** = Σ max(0, EXIT_i − due_i). block이 늦게 나갈수록 벌점.
- **Z2 = load imbalance** = bay 간 정규화 부하 편차의 최댓값. **bay 배정만으로 결정.**
- **Z3 = preference loss** = 선호 bay가 아닌 곳에 넣은 벌점. **bay 배정만으로 결정.**

여기서 실제 인스턴스의 가중치를 보면 (예: `w1 ≈ 26667, w2 = 10, w3 = 300`), **Z1이 목적함수의
약 99%를 지배**한다. Z2·Z3는 반올림 수준의 잔돈이다.

그런데 pipeline의 **가장 앞 결정** — 각 block을 어느 bay에 넣을지 — 는 Phase1에서 Z2·Z3
(그리고 약한 혼잡 항)를 최소화하도록 정해지고, 그 결정이 **배치 엔진이 돌기 전에 이미
고정**된다. 실제로 Phase1의 greedy 점수는([greedy.py:88-95](../Phase1/greedy.py#L88)):

```
score(i, j) = w2 · Z2_after(i→j) + w3 · (preference gap) + crowd(i, j)
```

즉 **잔돈(Z2·Z3)이 배치의 뼈대(bay 배정)를 선점**하고, 정작 목적함수를 지배하는 Z1
(tardiness)은 "이미 정해진 bay 배정을 물려받아 알아서 감당"하는 구조였다.

> **핵심 질문:** Z1이 목적함수의 전부인데, Z2·Z3가 자리 배치를 선제적으로 정하도록 두는 게
> 애초에 방향이 거꾸로 아닐까? pipeline의 모든 단계가 Z1을 의식하도록 바뀌어야 하는 것 아닐까?

이 질문 하나가 이후 모든 변경의 축이 되었다.

---

## 2. 대략적 사고 계획 (전체 로드맵)

"모든 단계를 Z1-aware하게"를 구체적 작업으로 쪼개면, Z1이 나빠지는 경로는 결국 **"급한
block이 제때 좋은 자리에 못 들어가서 늦게 나간다"** 하나로 모인다. 그 경로를 위에서 아래로
훑으며 병목을 하나씩 제거하기로 했다. 아래 네 갈래가 각각 앞 네 부(1~4부)가 되고, 그 뒤의
탐색·배포 정밀화와 인프라 정리는 [5부]가 다룬다.

1. **자리(공간)를 낭비하지 말자.** 배치 엔진이 자리를 못 찾으면 늦어진다. 기하 엔진이
   실제로는 쓸 수 있는 공간을 과보수적으로 "막힘"으로 치고 있지 않은지부터 본다.
   → *Phase2 raster.* **(이 문서 §3)**
2. **가장 앞 결정(bay 배정)을 Z1-aware하게.** 배정 단계가 "이 bay에 이 block을 더 넣으면
   나중에 붐벼서 누군가 늦는다"를 볼 수 있게 만든다. → *Phase1 + Outer.* **([2부])**
3. **고정 배정이라는 전제 자체를 깬다.** 배정이 아무리 좋아도 배치 시점의 실제 packing은
   모른다. 배치 엔진이 "이 배정으론 지금 늦는다"를 감지하면 배정을 덮어쓸 수 있게 한다.
   → *Phase2 배치 엔진.* **([3부])**
4. **같은 시간에 더 많이 시도하자.** 위 세 개를 켜면 탐색 1회가 무거워진다. 1회를 싸게
   만들고, 서로 다른 전략을 병렬로 돌려 best만 취한다. → *Phase2 + Outer.* **([4부])**

이 문서(1부)는 로드맵의 **1번**을 다룬다.

---

## 3. 변경 1 — 공간을 버리지 않기: free-space scan에서 과보수적 팽창 제거

**사고(계획 1):** 배치 엔진이 자리를 못 찾으면 그 block은 늦게 나간다 = Z1 악화. 그러니
밀도(얼마나 촘촘히 놓느냐)가 곧 Z1을 좌우한다. 가장 먼저, 기하 엔진이 **실제로는 쓸 수 있는
공간을 과보수적으로 "막힘"으로 치고 있지 않은지**부터 본다.

**어디를 바꿨나:** `Phase2/raster.py` — mask 생성과 전수 scan.

**논리:** 배치 엔진이 자리를 찾을 때, 각 (block, orientation)을 격자 mask로 만들고 그 mask가
bay의 점유 격자와 겹치지 않는 anchor를 "놓을 수 있는 자리"로 본다. 문제는 예전 scan이 점유를
**한 칸(8-이웃) 팽창(dilation)** 시켜, 서로 변이 맞닿는 "딱 붙는" 배치까지 충돌로 쳤다는 것이다.
이유는 "경계선이 맞닿을 때 crane 판정이 수치적으로 흔들릴까 봐"였는데, 다시 보니 그건
soundness 문제가 아니라 **후보를 버리는 낭비**였다. 왜냐하면 배치 엔진은 어차피 최종적으로
**정확한 crane gate**를 따로 통과시키기 때문이다(아래).

**바꾼 것:**
- mask는 각 layer의 **convex hull을 격자에 rasterize한 superset**으로 만든다
  ([raster.py:56-74](../Phase2/raster.py#L56), `Polygon(ring).convex_hull`). hull을 쓰는 이유는
  정확 gate가 쓰는 NFP가 hull 이하로 근사되어 있어서, mask가 hull의 superset이어야 "mask가
  겹치지 않으면 진짜로 안 겹친다"가 성립하기 때문이다(오목 노치만 쓰면 위반 가능).
- scan은 팽창 없이 **suffix-union**(층 k 이상의 점유 OR, [raster.py:114-129](../Phase2/raster.py#L114))과
  mask의 disjoint 여부만 본다([raster.py:164-237](../Phase2/raster.py#L164)). 이때 crane의 "위층이
  아래층 진입을 막는다(j ≥ k)" 규칙이 suffix-union으로 자연히 표현된다.

**soundness 논증(중요):** mask가 disjoint ⇒ hull 면적이 안 겹침 ⇒ polygon 내부가 안 겹침
(변·꼭짓점 접촉은 면적 0 = 합법) ⇒ 공간 충돌 없음 + crane 수직 진입 가능. scan이 증명하지
못하는 것은 **시간축뿐**이다 — 즉 "내가 나갈 때 위에 남아있는 block이 내 반출을 막는가",
"내가 지금 들어가면서 곧 나갈 상주의 반출을 막는가". 이 둘만 배치 엔진이 매 배치마다 정확
gate로 검사한다([dispatch.py:84-95](../Phase2/dispatch.py#L84), `_exact_gate` → `crane_obstructed`/
`crane_blocks_resident`). 즉 scan은 값싼 공간 prefilter, 시간축·crane 미세 판정은 정확 gate가
담당하는 역할 분리가 성립한다.

**얻은 것:** 버려지던 밀도 10–20%를 회수. 붐비는 문제들의 Z1이 −27~36%. (회귀 방지:
`tests/test_raster.py`가 모든 scan-feasible anchor가 실제 공식 체커 기준으로도 충돌 없음을
fuzz로 검증.)

---

**다음 →** [2부] 가장 앞 결정(bay 배정)을 Z1-aware하게 만들기 (혼잡 페널티).
