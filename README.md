# OGC 2026 — 조선소 블록 배치 스케줄링

블록(2D 폴리곤 layer들의 stack)을 bay에 배정하고, 위치·방향·진입일·진출일을 정해
가중 목적함수를 최소화한다. 진입점은 `myalgorithm.py`의 `algorithm(prob_info, timelimit)`.

---

## (매우 중요) 직관적 이해

### 목적함수

$$
\min\ \sum_{i=1}^{3} w_i \cdot Z_i
$$

$w_i$는 문제마다 주어지는 값으로, 세 요인 중 무엇을 우선할지 정하는 파라미터이자
scale을 맞추는 normalize 계수 역할을 겸한다.

### 각 $Z$의 의미

**$Z_1$ : 총 지연**

$$
Z_1 = \sum_i \max(0,\ \mathrm{EXIT}_i - D_i)
$$

각 블록이 납기 $D_i$를 넘긴 날 수의 합. 제때 나가면 0. 얼마나 늦는지는 타이밍·배치·크레인이
정하므로 **Phase 2에서 EXIT이 확정돼야** 나온다. 목적함수의 대부분(≈99%)을 차지한다.

**$Z_2$ : 작업장 부하 불균형**

$$
Z_2 = \max_{j_1 \neq j_2}\Big(u_{j_1}\!\!\sum_{i \in N(j_1)}\!\! L_i \;-\; u_{j_2}\!\!\sum_{i \in N(j_2)}\!\! L_i\Big)
$$

$$
u_j = \frac{\bar A}{W_j H_j},\qquad \bar A = \frac{1}{|M|}\sum_k W_k H_k
$$

bay마다 면적으로 정규화한 총 노동량을 구해, 가장 차이 나는 두 bay의 격차를 본다. 작은 bay에
일을 몰면 $u_j$가 커서 벌점이 크다. **bay 배정만으로 결정된다.**

**$Z_3$ : 선호 손실**

$$
Z_3 = \sum_i \big(S_i^{\max} - S_{i,\mathrm{bay}(i)}\big),\qquad S_i^{\max} = \max_j S_{ij}
$$

각 블록이 최선호 bay 대신 다른 bay로 가서 잃은 선호 점수의 합. **bay 배정만으로 결정된다.**

### 핵심 집합

$$
\begin{aligned}
N(j)   &= \{\, i : \mathrm{bay}(i)=j \,\} && \text{bay }j\text{ 배정 블록 (시간무관) — }Z_2,Z_3 \\
N(t,j) &= \{\, i : \mathrm{bay}(i)=j,\ \mathrm{ENTRY}_i \le t < \mathrm{EXIT}_i \,\} && \text{시각 }t\text{ 상주 블록 — 기하·크레인}
\end{aligned}
$$

### 전체 데이터 흐름

```
prob_info → [Phase 0 전처리] → PRE
                                 ↓
PRE → [Phase 1] → {bay, ENTRY, EXIT} + (Z2·Z3 확정)
                                 ↓
{bay, ENTRY, EXIT} → [Phase 2] → {(x,y), 방향 o, 크레인 순서}
                                 |→ 배치·크레인 실패 시 → ENTRY/EXIT 조정 → (Phase 2 재실행)
                                 ↓
완전한 해 → [평가 Z1·Z2·Z3] → ALNS(배정 재탐색) → 4-멤버 병렬 포트폴리오 → 제출
```

`realize(bay)` = Phase1 타이밍 → Phase2 배치/크레인 → 목적함수. 배정을 넣으면 결정적으로
정확한 $f$가 나오므로, ALNS는 **bay 배정 공간**만 탐색한다.

### 최종 구현 요약 (계획 대비 무엇을 확정/제거했나)

| 부분 | 확정 | 제거·미채택 |
|---|---|---|
| Phase 0 기하 | `fast` backend (HM convex-decomp Minkowski, 5–10× 빠름, 목적함수 동일) | `pieces`(목적함수 바뀜) |
| Phase 1 | **greedy 단독** (EDD + DFF 필요조건 게이트) | MIP·arcflow·τ₁ split·size 임계 |
| Phase 2 순서 | area(어려운 것 먼저) 기본, **MST**는 포트폴리오 멤버 | 동적 재선택 |
| Phase 2 점수 | **CONTACT** (가중 blend + S-curve) | CORNER·BCA·HYBRID |
| Phase 2 크레인 | **reactive** (자유 배치 + 증분 repair) | proactive lookahead·LBBD cut |
| Outer | ALNS + **4-멤버 병렬 포트폴리오** | LBBD coupling |

핵심 성능 레버는 **4코어 병렬 포트폴리오**(단일 ALNS는 1코어만 쓴다)와 **`fast` 기하 backend**다.

### Shapely / networkx를 hot loop에서 왜 안 쓰나

- **Shapely**: 최종 인증(제출 직전 서버와 일치 확인) 한 곳에서만 쓴다. hot loop에서는 GEOS(C)
  경계 왕복·객체 할당 오버헤드가 과해, 순수 파이썬 기하(+선택적 numba 커널)로 대체했다.
- **networkx**: blocking/clique 그래프는 노드가 수십 개로 작아, 라이브러리 호출 오버헤드가
  실제 계산보다 크다. 직접 interval-graph 스윕으로 처리한다.

---

## Phase 0) 전처리 `Phase0/`

### 0.1 스칼라 상수 `constants.py`

- 입력: `prob_info`
- 하는 일: 자주 쓰는 상수를 미리 계산

$$
\begin{aligned}
u_j &= \bar A / (W_j H_j) && \text{bay 면적가중 (작을수록 큼)} \\
S_i^{\max} &= \max_j S_{ij} && \text{최선호 점수} \\
\mathrm{EST}_i &= R_i,\quad \mathrm{LST}^0_i = D_i - P_i && \text{최조기·정시 최늦 진입일} \\
\mathrm{slack}_i &= D_i - R_i - P_i && \text{여유일 (작을수록 긴급)}
\end{aligned}
$$

- 출력: `u, Smax, EST, LST0, slack`

### 0.2 기하 자료구조 IFP·NFP `geometry.py`, `geometry_tables.py`

- 입력: 블록×방향 layer 좌표, $W_j, H_j$
- 하는 일: (필요 시)정점 단순화 → IFP 사각형 → 동시점유 쌍의 NFP를 **lazy** 계산·캐시

$$
\begin{aligned}
\mathrm{IFP}_{i,o,j} &= [\lceil -lx_0\rceil,\ \lfloor W_j - lx_1\rfloor] \times [\lceil -ly_0\rceil,\ \lfloor H_j - ly_1\rfloor] && \text{bay 안에 들어오는 ref 영역} \\
\mathrm{NFP}_{AB} &= A \oplus (-B) && \text{Minkowski; 경계=touch, 내부=중첩}
\end{aligned}
$$

- **확정**: `GEOM_MODE="fast"` — Hertel–Mehlhorn 볼록분해 후 순수 파이썬 Minkowski, 다각 조각만
  shapely union. `shapely` 참조 경로와 ring이 동일함을 검증(목적함수 bit-identical)했고 5–10× 빠르다.
- **확정**: `DP_TOL=0.0` (단순화 끔). 서버는 원본 폴리곤으로 인증하므로, 안쪽으로 줄이는 단순화는
  false-feasible 위험이 있어 안전값으로 둔다.
- 출력: (단순화)layer, IFP 표, NFP 캐시

### 0.3 동시점유 clique `cliques.py`

- 입력: 후보 ENTRY/EXIT
- 하는 일: bay 안 시간 겹침 maximal clique(≤ n개) 추출 (interval graph 스윕)
- 출력: bay별 clique 목록

---

## Phase 1) BuildBayAssignment `Phase1/`

> **왜 여기서 $Z_2, Z_3$가 확정?** 두 값은 배정집합 $N(j)$만의 함수라 좌표·타이밍과 무관하기 때문.

### 1.0 greedy 배정 `greedy.py` (EDD + DFF 게이트)

MIP/LBBD은 **미채택**(exact가 heavy-tail이라 30분 예산엔 불안정, greedy가 안정적으로 우수).
greedy 단독으로 확정했다.

- 입력: `load_j, slack_i`, bay 크기
- 하는 일: EDD 순으로 배정. bay마다 DFF 필요조건(면적 하한)을 통과하는 후보에만 시도하고,
  가중 목적 증분이 최소인 bay를 고른다.

$$
\begin{aligned}
\text{정렬} &:\ (\mathrm{slack}_i \uparrow,\ D_i \uparrow) && \text{(EDD, tardiness 표준 규칙)} \\
j^\ast &= \mathop{\arg\min}_{j:\ \mathrm{elig}_{ij},\ \mathrm{DFF}} \big[\alpha_h w_2\,\Delta Z_2(j) + \beta_h w_3 (S_i^{\max} - S_{ij})\big]
\end{aligned}
$$

- **확정값**: $\alpha_h=\beta_h=1.0$, `use_dff=True`, DFF 격자 step $=0.1$ over $(0,0.5]^2$.
  $\Delta Z_2$는 블록 $i$를 넣었을 때의 정확한 불균형 재계산(myopic). 후보가 없으면 최후수단으로
  가장 큰 eligible bay.
- 출력: `bay[i]`

### 1.1 타이밍 초기화 `timing.py` → 잠정 $Z_1$, 확정 $Z_2 Z_3$

- 하는 일: 조기 진입으로 EXIT 파생

$$
\mathrm{ENTRY}_i = \mathrm{EST}_i,\quad \mathrm{EXIT}_i = \mathrm{ENTRY}_i + P_i,\quad
Z_1^{\text{잠정}} = \sum_i \max(0, \mathrm{EXIT}_i - D_i)
$$

- 출력: `{bay, ENTRY, EXIT}`, $Z_2 Z_3$ 확정, clique → Phase 2

---

## Phase 2) PlaceAndCrane `Phase2/`

### 2.0 상주집합·극대시점 `residents.py`

$$
\mathrm{SB}[t] = \{ i : \mathrm{ENTRY}_i \le t < \mathrm{EXIT}_i \},\qquad
\mathrm{TT}(j) = \{ t : \mathrm{SB}[t]\ \text{가 다른 시점에 포함되지 않는 극대시점} \}
$$

극대시점(event day)만 검사하면 되므로 전 구간 스캔을 피한다. 출력: `SB[t], TT(j)`.

### 2.1 배치 순서 `ordering.py`

- **확정**: 정적 정렬(1회). 어려운(큰) 블록 먼저.

$$
\text{order} = \text{sort}_\downarrow(\lambda_1 \cdot \mathrm{area}),\qquad \lambda_1 = 1.0
$$

- **포트폴리오 멤버**: `order_mode="mst"` — 최소 slack(긴급) 블록 먼저. 긴급 블록이 강제 슬롯을
  피하게 해 $Z_1$을 직접 줄인다. 고정 default는 아니고 포트폴리오 한 멤버로 쓴다.

### 2.2 후보 위치 `candidates.py`

- 하는 일: bay 안(IFP) ∩ 모든 상주 NFP 밖의 정수 정점 후보 생성

$$
\bar L = \left\{\, (x,y)\in \mathrm{IFP}_{i,o,j} \;:\; (x,y)\notin \operatorname{int}\mathrm{NFP}(p_n, p_i^o)\ \ \forall\, p_n\in \mathrm{SB}[t] \,\right\}
$$

- **확정**: BLF seed(IFP 좌하단 코너) + 상주 NFP 경계 정점. 교점 추가는 미채택(후보수 대비 이득 적음).
- 출력: 후보 위치 리스트

### 2.3 충돌 오라클 + 배치 점수 `collision.py`, `scoring.py`

- 오라클: **bbox Test 1 선행**(대부분 쌍을 O(1)에 제거) → 생존 쌍만 layer별 point-in-NFP.
- 점수: **CONTACT 모드 확정**(가중 blend + S-curve). CORNER/BCA/HYBRID는 미채택.

$$
\begin{aligned}
\mathrm{score} &= w_{ct}\,\mathrm{contact} + w_{cn}\,\mathrm{corner} + w_{tp}\,\mathrm{temporal} + w_{pm}\,\mathrm{premarsh} \\
w_m &= 1 - \frac{1}{1 + e^{(2m - G)/(2K)}} && \text{(S-curve: 초반 밀착 → 후반 fit)}
\end{aligned}
$$

- **확정값**: $w_{ct}=1.0,\ w_{cn}=0.01,\ w_{tp}=0.1,\ w_{pm}=1.0,\ K=4.0$.
- 출력: $(x_i, y_i, o_i)$

### 2.4 크레인 feasibility `crane.py`

- 하는 일: 진입/진출 시 layer-$k$가 기존 layer-$j\,(j\ge k)$와 겹치면 막힘. 재배치 금지(#RS=0 hard),
  당일은 EXIT 먼저(LIFO), blocking deadlock 검사.

$$
\text{진입 차단} \iff \exists\, p_n,\ j\ge k:\ \mathrm{layer}_k(p_i)\cap \mathrm{layer}_j(p_n)\neq\varnothing
$$

- **확정**: reactive — 자유롭게 배치하고 막히면 2.5의 증분 repair에 맡긴다. proactive lookahead·
  LBBD cut은 미채택(구현 복잡도 대비 이득 불확실). 최종 인증은 서버 `utils.check_entry/exit`.
- 출력: 크레인 in/out 순서, feasible 플래그

### 2.5 실패 처리 · victim · 안전망 `repair.py`, `driver.py`

- 하는 일: 국소 시간 조정으로 재시도하고, 항상 완전 feasible 해 1개를 유지한다.

$$
\begin{aligned}
\text{배치 실패} &:\ \mathrm{ENTRY}_i \mathrel{+}= 1,\ \mathrm{EXIT}_i = \mathrm{ENTRY}_i + P_i \\
\text{크레인 막힘} &:\ \mathrm{EXIT}_i \mathrel{+}= 1\ \ \text{또는}\ \ \mathrm{ENTRY}_i \mathrel{+}= 1
\end{aligned}
$$

- **확정(victim)**: 구조적 LIFO 대신 **$Z_1$ 증분이 최소인 블록**을 미룬다(목적함수가 직접 정의).

$$
\mathrm{victim} = \mathop{\arg\min}_{k}\big[\max(0, \mathrm{EXIT}_k + 1 - D_k) - \max(0, \mathrm{EXIT}_k - D_k)\big]
$$

- **강제 배치(`forcing_mode`)**: 기본 `empty_bay`(빈 window에 강제), 포트폴리오 멤버로
  `earliest_slot`(현재 상주 사이 가장 이른 feasible 시점). 고분산이라 default가 아닌 멤버로 둔다.
- `max_repair_passes=2` 후에도 실패하면 안전망이 완전 feasible 해를 보장.

### 2.6 intra-bay 개선 `improve.py`

- **확정**: 기본 off. 포트폴리오 멤버로 `jostle_2exchange`(Jostle + 2-exchange 조밀화,
  `improve_rounds=2`). 고분산(패킹은 좋아지나 목적함수 효과는 인스턴스별)이라 멤버로만 쓴다.
- 출력: 개선된 $(x,y,o)$ → 실현 EXIT → $Z_1$ 확정

---

## OUTER) ALNS + 병렬 포트폴리오 `Outer/`

배정 공간을 destroy→repair→realize→SA-accept로 반복 개선하고, 이 ALNS를 4개 변형으로 병렬 실행한다.

### O.1 Destroy `destroy.py`

- 하는 일: roulette로 연산자(random/worst/related)를 골라 $q=\xi n$개 제거.

$$
R(i,j) = \phi[\mathrm{bay}(i){=}\mathrm{bay}(j)] + \chi|\mathrm{ENTRY}_i - \mathrm{ENTRY}_j| + \psi\,\mathrm{prox} + \omega\,\mathrm{block}
$$

- **확정값(튜닝 1순위)**: $\xi=0.4$ (Ropke–Pisinger interior optimum). $\phi{=}\chi{=}\psi{=}\omega{=}1$,
  결정성 지수 $p_{\text{shaw}}{=}p_{\text{worst}}{=}6$.

### O.2 Repair `repair.py`

- 하는 일: greedy 또는 regret-$k$로 Phase 1·2를 거쳐 재삽입.

$$
\text{Greedy}:\ \min_i c_i,\qquad \text{Regret-}k:\ \max_i \sum_{l=1}^{k}\big(\Delta f_{i,x_{il}} - \Delta f_{i,x_{i1}}\big)
$$

- **확정값**: `regret_k=3`, 삽입비용 noise $=0.1$ (없으면 결정적 greedy가 파괴한 배정을 그대로
  복원 → 이웃이 한 점으로 붕괴).
- **$Z_1$-인지 crowding 페널티**: repair는 $Z_1$(Phase 2에서만 실현)에 blind하므로, 과밀·강제
  배정을 피하도록 용량초과분에 소프트 페널티를 준다. `crowd_weight=1.0, eta=0.85`(0이면 기존 blind).

### O.3 연산자 선택 (adaptive-weights) `operators.py`

- **확정**: adaptive-weights (Q-learning AOS는 미채택 — 소파라미터로 충분, 우월 근거 부족).

$$
P(j) = \frac{w_j}{\sum_i w_i},\qquad w_{i} \leftarrow w_{i}(1-r) + r\,\frac{\pi_i}{\theta_i}
$$

- **확정값**: $r=0.1$, 보상 $\sigma_1{=}33$(신규 best) $>\sigma_3{=}13$(악화 수락, 다양화)
  $>\sigma_2{=}9$(개선 수락), segment $=100$ iter.

### O.4 Acceptance (SA) `acceptance.py`, `alns.py`

$$
\Pr[\text{accept}] = \exp\!\Big(-\frac{f(s') - f(s)}{T}\Big),\qquad T \leftarrow T\cdot c
$$

- **확정값**: $c=0.99975$. $T_{\text{start}}$는 파라미터가 아니라 초기해 대비 $w{=}5\%$ 악화를 확률
  0.5로 수락하도록 역산. LBBD coupling은 미채택(local repair만).

### O.5 병렬 포트폴리오 (핵심) `portfolio.py`, `worker.py`

단일 ALNS는 허용된 4코어 중 1개만 쓴다. 서로 다른 4개 멤버를 **독립 OS 서브프로세스**로 동시에
돌리고 best를 취한다 → 같은 wall-clock에 Virtual Best Solver를 실현하며, 멤버 0(안전 baseline)
덕분에 **결과가 baseline보다 나빠질 수 없다.**

| 멤버 | 2.6 | forcing | order | 역할 |
|---|---|---|---|---|
| 0 | off | empty_bay | area | 안전 baseline (floor) |
| 1 | jostle | empty_bay | area | 패킹 개선 |
| 2 | jostle | empty_bay | **mst** | 긴급도 + 패킹 |
| 3 | off | **earliest_slot** | **mst** | 긴급도 + 강제완화 |

- **독립 서브프로세스인 이유**: 대회 tester가 `algorithm()`을 `__main__` 가드 없는 서브프로세스에서
  부르는데, `multiprocessing` spawn Pool은 그 `__main__`을 워커마다 재실행(중첩 포트폴리오)한다.
  독립 프로세스는 각자 가드된 `__main__`을 가져 안전하다.
- **부모가 PRE를 warm+pickle하는 이유**: 워커 4개가 NFP 캐시를 동시에 cold로 만들면 경합으로 각
  첫 realize가 크게 느려진다. 부모가 한 번 warm한 PRE를 pickle로 공유해 이를 없앤다. 이 warm 결과가
  guaranteed feasible floor도 된다.
- 성능 최적화(모두 목적함수 bit-identical): `fast` 기하 backend, point-in-ring·shared-edge numba
  커널, reflected-NFP memo, world_bbox 사전계산.

---

## 실행 / 제출

```python
from myalgorithm import algorithm
solution = algorithm(prob_info, timelimit)   # {"operations": {...}}
```

- 제출물: `myalgorithm.py` + `Phase0/ Phase1/ Phase2/ Outer/`.
- `utils.py`는 평가 서버가 제공(로컬은 `ogc2026/baseline/utils.py` 폴백).
- 인스턴스 데이터(`train/`, `data/`)와 대회 tester(`ogc2026/`)는 `.gitignore` 처리(디스크엔 유지).
