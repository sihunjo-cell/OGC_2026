# 설계 변천 ⑥ — 변경 5·6: 어떤 규모에서도 완주하기 + mask가 못 보던 자리 되찾기

> **설계 변천 5부작 + 후속** — [1] 전체 흐름 · 밀도 회수 · [2] 배정을 Z1-aware하게 · [3] 배정 고정 전제 깨기 · [4] 탐색 가속·다양성 · [5] 탐색·배포 정밀화·인프라 정리 · **[6] 완주 보장 · 비가시 anchor 회수**
>
> 전체 흐름·문제의식·로드맵은 [1부](EVOLUTION1.md) 참고.

> ▸ **[5부]까지로** 밀도 회수 · Z1-aware 배정 · 동적 bay · 탐색 가속 · 배포 안전판(dyn-on/off
> 페어, stall-restart)이 섰다. 그 위에서 두 가지가 새로 드러났다. 하나는 **운영의 자격 조건**
> — 아무리 좋은 탐색도 시간 안에 feasible을 못 돌려주면 0점이다. 다른 하나는 [1부]가 남겨둔
> **계약의 가격표** — soundness를 위해 mask를 convex hull superset로 유지했는데, 그 보수성이  
> 실제로 얼마를 잃게 하는지 한 번도 재보지 않았다. 이번 장은 이 둘의 해소다.

---

## 사고 — 5부작 이후 두 관찰

**관찰 1 (운영).** 채점 규칙상 반환은 timelimit 안에 이뤄져야 하고, 늦으면 해 품질과 무관하게
실격이다. 그런데 block 수가 큰 인스턴스로 세 갈래 사망 경로가 실측됐다:
① 파이프라인 어딘가 실패하면 None을 내거나 전체를 재실행해 시간을 초과, ② 마감 직후 남은
block들을 강제 배치하는 마무리 단계가 bay당 O(k²) 꼬리가 되어 초과, ③ scan·NFP 캐시가
무한성장해 메모리 사망. **탐색 품질 이전에, "어떤 규모·어떤 실패에서도 시간 안에 feasible
완주"를 구조적으로 보장해야 한다.** → 변경 5.

**관찰 2 (알고리즘).** [1부]에서 팽창(dilation)을 제거할 때, mask 자체는 **convex hull의
superset**으로 남겨야 했다 — 정확 gate의 NFP가 hull 이하로 근사되어 있어, mask ⊇ hull이어야
"mask가 disjoint면 진짜로 안 겹친다"가 성립하기 때문이다(soundness 계약). 그 계약의 **가격**을
이번에 처음 재봤다. 오목한 block이 많은 문제에서 hull은 실제 polygon보다 면적 가중 **+9~11%**
부풀어 있었고, 더 결정적으로 — 저장된 해 위에서 배치 엔진의 눈(hull scan)과 정확한 polygon
판정을 **같은 crane gate**로 나란히 돌리는 오프라인 프로브([diag_nestle.py](../tools/diag_nestle.py))를
만들어 재보니, **엔진이 "자리 없음"이라 선언한 순간의 8~12.6%** (측정 6문제 전부)에서 실제로는
가능한 배치가 존재했다. 오목한 홈에 딱 **안기는** 자리들 — mask에는 원리적으로 안 보이는 밴드다.
자리를 못 찾은 block은 대기하고, 대기는 곧 tardiness(Z1)다. → 변경 6.

교훈은 하나로 요약된다: **공짜처럼 보이는 계약(제약)에도 가격표를 달아 측정하라.** [1부]의
질문("과보수적으로 막고 있지 않은가")은 팽창 제거로 끝난 게 아니었다.

---

## 변경 5 — 어떤 규모에서도 시간 안에 완주 (반환 보장 · 마감 후 O(1) · 메모리 상한)

**어디를 바꿨나:** `myalgorithm.py`(진입점) + `Outer/portfolio.py`·`worker.py`·`alns.py` +
`Outer/floor.py`(신설) + `Phase2/dispatch.py`·`repair.py` + `Phase2/raster.py`(캐시).

**논리:** 세 사망 경로 각각에 **결정론적** 방어를 세운다. 원칙은 "마감 전 궤적 불변" — 시간
방어는 마감 이후에만 개입하므로, 정상 실행의 탐색 궤적·결과는 byte 단위로 그대로다.

**바꾼 것:**

- **최후의 floor** ([floor.py](../Outer/floor.py)): 각 block을 선호 bay의 IFP 코너에, 그 bay가
  비는 시간 창(tail-pointer)에 놓는 배치 — 빈 창 + 코너라 구조적으로 체커-feasible이고 절대
  실패하지 않는다. `optimize_portfolio`는 warm·워커가 전부 죽어도 None 대신 이 floor를 반환하고
  ([portfolio.py:110-123](../Outer/portfolio.py#L110)), 진입점도 마감이 소진된 뒤에는 파이프라인
  재실행(그 자체가 초과 사망 경로) 대신 만들어 둔 floor를 낸다
  ([myalgorithm.py:47-58](../myalgorithm.py#L47)). floor는 min-비교에서 절대 못 이기므로 정상
  경로 결과는 불변.
- **증분 원자 기록** ([worker.py:38-54](../Outer/worker.py#L38), [alns.py:39-53](../Outer/alns.py#L39)):
  워커가 best를 갱신할 때마다 tmp 파일 + `os.replace`로 기록한다. 워커가 도중에 죽어도 부모는
  항상 "완전한 파일로 남은 마지막 best"를 수확한다 — 전부-아니면-전무를 없앴다.
- **마감 후 O(1) 완결** ([dispatch.py:355-368](../Phase2/dispatch.py#L355),
  [repair.py:46](../Phase2/repair.py#L46)): 잔여 block 강제 배치가 마감 후에는 빈-창 탐색(bay당
  O(k²)) 대신 bay 꼬리에 붙이는 tail-pointer(O(1))로 전환된다. 잘린 해는 min-wins에서 자연
  도태되므로 품질 영향 없음.
- **예산 방어선 두 겹**: warm 빌드에 상한 max(30s, 0.35·T)를 걸어 대형에서 워커 예산을 보호
  ([portfolio.py:101-105](../Outer/portfolio.py#L101)), 워커도 부모의 절대 deadline을 자기 시계
  예산으로 다시 상한한다([worker.py:68-72](../Outer/worker.py#L68)).
- **메모리 상한**: scan 캐시에 바이트 예산(초과 시 전량 clear — miss는 재계산이라 byte 동일,
  [raster.py:48](../Phase2/raster.py#L48)·[175](../Phase2/raster.py#L175)), ALNS 방문집합 키를
  O(n) 튜플에서 16B digest로([operators.py:14-17](../Outer/operators.py#L14)), [5부] §4에서
  "잔존"시켰던 동시존재 후보쌍 표(CO, O(n²))는 이번에 제거 완결.

**얻은 것:** block 수를 8배로 부풀린 스트레스 프록시에서 **133s(예산 초과, 강제종료 재현) →
116s(예산 내)**. 전 규모에서 feasible 완주 + 메모리 상수 상한. 이후의 모든 변경(변경 6 포함)은
이 보장 위에서만 켜진다.

---

## 변경 6 — mask가 못 보던 자리 되찾기 (scan 전멸 시에만 정밀 재검사)

**사고:** mask를 tight하게 다시 만들면 soundness 계약이 깨진다(NFP 근사와의 정합). 그래서
계약은 그대로 두고 **역할 분리를 한 단 더** 나눈다: 평소에는 기존 scan(값싼 prefilter)을 그대로
쓰고, **scan이 한 자리도 못 찾은 그 순간에만** — 프로브가 "여기에 실제 자리가 있다"고 알려준
바로 그 순간에만 — 겹침이 얕은 anchor 소수를 정확한 polygon으로 재검사해서, 통과하면 넣는다.
실패 순간은 소수라 비용이 국소적이고, 성공 한 번의 가치는 크다(대기 → 즉시 배치 = tardiness
직접 절감). K(겹침 깊이 상한)의 근거도 프로브에서 나왔다: 회수 가능 anchor의 mask 겹침 카운트
분포 상위 90%가 24~68 → K = 32.

**어디를 바꿨나:** `Phase2/raster.py`(count 그리드) + `Phase2/dispatch.py`(재검사 훅) +
`Phase2/nestle.py`(신설, 정밀 판정) + `Phase2/config.py`(knob) + `Outer/portfolio.py`(배포).

**바꾼 것:**

- scan은 원래 anchor별 겹침 카운트를 계산해 놓고 (total == 0)만 남기고 버렸다. 그 카운트
  그리드를 그대로 반환하는 `count_scan`을 추가([raster.py:260](../Phase2/raster.py#L260)).
- admission에서 mask 패스가 **전멸했을 때만** `_try_nestle`
  ([dispatch.py:113-160](../Phase2/dispatch.py#L113), 훅 [207](../Phase2/dispatch.py#L207)):
  0 < count ≤ K인 anchor를 count 오름차순으로 cap(=12)개까지, ① 정확 polygon 판정(후보 layer k
  vs 상주 layer ≥ k 합집합, 변 접촉 = 교차면적 ≤ 1e-9 = 합법) ② 기존 `_exact_gate`(시간축
  crane) 순으로 통과시키고, 커밋은 **기존 경로 그대로**(hull mask 스탬프 유지).
- 정밀 판정은 `ExactSpace`([nestle.py](../Phase2/nestle.py)) — bay별 상주 합집합 기하의 lazy
  캐시로, 점유 버전(ver)이 바뀌면 자동 무효화.
- knob: `dispatch_nestle_k=32`, `dispatch_nestle_cap=12`
  ([config.py:29-34](../Phase2/config.py#L29)). **K=0이면 경로 자체에 진입하지 않아 byte
  동일**(안전한 A/B). 재검사 비용은 디코드당 FLOP 예산으로 자기제한 — 초과하면 잔여를
  결정론적으로 끈다(대형 인스턴스 자동 셧오프 = 변경 5의 완주 보장과 양립).
- 배포는 dyn-on 두 워커에만 기본 적용([portfolio.py:43-53](../Outer/portfolio.py#L43)) —
  dyn-off floor 워커는 무접촉이라 [5부] §1의 구조적 무회귀 보험이 그대로 선다.

**soundness 논증:** 회수된 anchor도 ① polygon 내부 서로소(변 접촉은 면적 0 = 공식 체커 기준
합법) ② 동일한 시간축 crane gate 통과 ③ 커밋 시 hull mask를 그대로 스탬프 — 이후 모든 scan의
보수성이 유지된다. 신뢰 표면이 새로 생기지 않는다. (회귀 방지: [tests/test_nestle.py](../tests/test_nestle.py)가
프로브가 실측한 회수-가능 anchor들을 fixture로 판정-동치를 고정.)

**얻은 것:** 같은 예산·같은 부하에서 켠/끈 쌍을 **동시에** 돌리는 짝비교(T=300, 붐비는 3문제 ×
전략 구성 2종 × seed 3개 = 54쌍)에서 **54쌍 전부 개선, 문제 평균 −16~28%**. 배포 구성 전체로는
전 40문제 60s에서 **−13.0%** — leave-one-out 교차검증으로 재선택해도 같은 구성이 뽑혀(과적합
신호 0), 이득은 비혼잡 문제까지 광범위했고(−31~38%짜리가 5문제) 회귀는 노이즈급 소수였으며
고-w3 floor군은 무회귀. 긴 예산(T=600)에서도 −21.7%·−9.6%로 유지. **단일 변경으로는 동적
bay([3부]) 이후 최대.**

---

## 변경 6b — 정밀 재검사를 거의 공짜로 (판정-동치 가속)

**사고:** 회수의 대가로 ALNS 반복수가 30~38% 줄었다. profile을 떠 보니 비용의 몸통은 둘 —
count 그리드를 매번 전량 재계산(realize 8.65s 중 2.2s), 그리고 정밀 판정이 후보마다 범용 기하
라이브러리를 부르는 스칼라 루프(2.05s). 반복수는 [4부]가 세운 자산이라 되찾아야 한다. 단,
**판정이 1비트도 달라지면 안 된다** — 속도를 위해 판정을 바꾸면 soundness 논증이 무너진다.

**바꾼 것:**

- `count_scan`에 scan과 동형의 (ver, 공간 국소 증분) 캐시([raster.py:260-311](../Phase2/raster.py#L260)):
  부분영역 갱신이 전체 재계산과 **비트동일**, 바이트 예산은 scan 캐시와 공유.
- 정밀 판정의 fast 경로([nestle.py:84-106](../Phase2/nestle.py#L84)·
  [233-260](../Phase2/nestle.py#L233)): 후보/상주 polygon을 볼록조각으로 평탄화하고(이미 있는
  NFP용 볼록분해를 재사용), **쌍별 볼록 교차면적의 합**을 numba 컴파일 루프(Sutherland–Hodgman
  클리핑)로 계산한다. 조각들이 내부-서로소라 Σ쌍면적 = (합집합 ∩ 후보) 면적 — 수학적으로 같은
  양이다. 문턱(1e-9) 근방의 좁은 razor band만 기준 구현(shapely)으로 재판정해 **판정 분기가
  동치**이고, numba가 없으면 통째로 기준 구현으로 폴백한다(`dispatch_nestle_fast`).

**얻은 것:** realize 8.65 → 3.95s(−54%), 5.36 → 3.21s(−40%). 짧은 예산에서 반복수 2.0×(잃었던
반복수 원복+α), 전 배치·목적함수 **비트 동일**(count 퍼즈 비트동일 + 판정동치 208케이스 +
realize on/off 끝단 동일, [tests/test_nestle_fast.py](../tests/test_nestle_fast.py)).

> 설계 대안 메모: 같은 시기에 "미래의 큰 block이 쓸 자리를 덜 죽이는 anchor를 선호"하도록
> anchor 점수에 파편화 벌점을 결합하는 축도 시험했다(admission pass 앞에서 대기열 대형 block들의
> feasible 지도를 적분영상으로 요약해 벌점화). 궤적을 바꾸는 레버인데 현 예산대에선 이득이
> 재현되지 않아 flag-off로 보관했다(긴 예산 전용 재평가 조건, [config.py:22-28](../Phase2/config.py#L22)).
> **이미 잃고 있던 자리의 회수(변경 6)가 앞으로 잃을 자리의 예방(벌점)보다 먼저**라는 순서가
> 이번 장의 결론이다.

**전망 한 줄:** count-임계 회수는 부분 회수다 — 자유공간 경계의 anchor를 직접 열거하면(정확
NFP 기계는 이미 있다) 이 밴드를 절단 없이 전부 회수할 수 있고, 6b의 fast 경로가 그 비용을
감당할 발판이다.

---

**이전 ←** [5부] 탐색·배포 정밀화와 인프라 정리
**다음 →** [7부] 해부 계기 · 형성기 개입 · 재검사 예산 재보정
**처음 →** [1부] 전체 흐름 + 밀도 회수
