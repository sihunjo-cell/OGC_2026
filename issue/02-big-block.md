# tardiness의 87~97%가 대형 block이다 — 소형 block이 대형의 마지막 연속 free space를 잠식하지 못하게 하자

## 배경

6문제를 해부한 결과 두 사실이 전 문제 공통으로 나왔다.

* **tardiness(Z1)의 87~97%가 중위 면적보다 큰 block**에서 나온다.
* admission 실패의 96~99%는 충돌·crane 문제가 아니라 **no_space** — 빈 면적은 있는데 그 block이
  들어갈 만큼 **연속된** free space가 없다.

배치 가능한 bay 수는 병목이 아니다(대형 block도 평균 2.7~4.0개 bay에 들어갈 수 있음). 문제는
어느 bay든 시간이 갈수록 free space가 조각나는 것이고, 소형 block은 조각에도 들어가지만 대형
block의 자리는 한 번 사라지면 다시 안 생긴다.

극단 사례가 prob_26이다: 최대 block이 bay의 27%, bay의 10% 이상인 block이 36개(전체 면적수요의
46%). 면적만 보는 낙관 plan조차 우리 실제 결과보다 나쁠 정도로 **모양 자체가 지배하는** 문제다.

현재 배치 위치 선택은 [raster.py의 `order_cells`](../Phase2/raster.py)가 담당한다: 배치 가능한
칸들을 **contact 점수**(이웃 block·벽과 얼마나 붙는가) 순으로 정렬해 상위 몇 개만 정밀 검사에
넘긴다. contact 최대화는 밀착 packing에는 좋지만, **"이 자리가 나중에 올 대형 block의 마지막
자리인지"는 전혀 보지 않는다.**

## 현재 문제

### 1. contact 점수의 사각지대

소형 block이 큰 free space의 한가운데(혹은 입구)에 앉아도 contact 점수는 말리지 않는다. 결과:
큰 연속 free space가 두 조각으로 갈라짐 → 곧 도착할 대형 block이 못 들어감(no_space) → 대기 →
tardiness. 위 실측(tardiness의 87~97% = 대형 block)의 직접 원인 후보다.

### 2. 같은 발상의 항을 과거에 실험해 이겼다가, 기반 교체 때 걷어냈다

"작은 조각 자리(pocket)에 우선 배치해 큰 연속 영역을 보존"하는 best-fit region 항을 과거 고정-bay
시절에 실험해 이긴 기록이 있다(28 −3.8%, 26 −2.5%, 30 −4.8%, 3시드 중 2시드 생존). 이후 dynamic
bay로 기반이 바뀌면서 코드를 정리했고, "새 기반 위 재검증 필수"로 남겨 두었다. dynamic bay는 bay
**사이**를 건너는 해법이라 bay **안**의 조각남은 그대로다 — 두 메커니즘은 겹치지 않는다.

### 3. 재도입 시 배포 환경 함정 하나

free space 크기 지도를 만들 때 외부 라이브러리(scipy의 연결영역 라벨링)가 편하지만 평가 서버에
없을 수 있다. 폴백(numpy 홍수 채우기 또는 항 생략)이 필수다.

## 개선 방향

### A. free space 보존 항 재도입 (best-fit region)

**어디를 바꿀까:** [raster.py `order_cells`](../Phase2/raster.py)에 항 하나.

```text
칸 점수 = contact 점수 − w × (그 칸이 속한 free space 조각의 크기 / bay 면적)
```

같은 contact면 **작은 조각**에 앉는 배치를 선호 → 큰 연속 free space는 대형 block 몫으로 남는다.
free space 크기 지도는 bay 상태 버전으로 캐시(상태가 바뀔 때만 재계산). scipy 부재 폴백 필수.
플래그 게이트(0 = off = 현행과 동일 결과).

### B. 대형 block 자리 예약(reservation) (공격적 대안)

곧 release될 대형 block의 필요 면적만큼 큰 free space를 임시로 잠가 소형 배치를 차단. 구현·부작용
위험이 A보다 훨씬 크므로 A가 무효일 때만.

## 우선순위

**A**. 근거: (1) 병인(free space 잠식)과 처방(free space 보존)이 정확히 대응하고, (2) 같은
메커니즘이 과거 실험에서 이긴 전력이 있으며, (3) 수정이 함수 하나에 항 하나로 국소적이고 off시
동작 불변, (4) 검증 절차가 확립돼 있다(부하-쌍대 A/B, 배포 예산에서 — 짧은 예산 결과만 믿지 말 것).

## 기대 효과

* 대형 block의 no_space 대기 감소 → tardiness의 최대 원천(87~97%)을 배치 위치 선택 단계에서 직격.
* 특히 prob_26(모양-지배 극단) — 이 문제는 admission 순서 plan([01-burst-plan](01-burst-plan.md))
  으로도 안 닫히는 기하 병목이라, 이 항이 유일한 대응 수단.
* dynamic bay(bay 사이) × free space 보존(bay 안)의 상보 조합 완성.
