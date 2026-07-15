# burst window 안의 admission 순서가 근시안적이다 — plan을 먼저 세우고 urgency가 plan을 보게 하자

## 배경

배치 엔진([dispatch_construct](../Phase2/dispatch.py))은 시간을 하루 단위 event로 진행하며, 매
event마다 대기 중인 block들을 **ATC urgency 점수**(slack이 작을수록, processing time이 짧을수록
높음) 순으로 정렬해 자리가 있으면 즉시 admission한다. 자리가 나는 즉시 채우므로(work-conserving)
"지금 이 순간"의 결정은 낭비가 없다.

그런데 인스턴스를 해부해 보니, 남은 문제들은 전부 **하나의 거대한 burst window(과부하
구간)**를 가진다 — release 즉시 넣는다고 가정하면 특정 30~40일 구간의 필요 면적이 bay 총면적을
초과한다:

burst가 깊을수록 성적이 나쁘다. 즉 승부는 그 window 안에서 **"누구를 먼저 넣고 누구를 기다리게
할지"**에서 갈리는데, 지금 그 결정은 매 시점의 국소 urgency가 전담한다.

면적만 보는 낙관 plan(capacity relaxation: due date 순으로 일별 남은 면적에 채우기)을 세워 보면,
tardiness를 지금의 1/2.2~1/3.5 수준으로 만드는 admission 일정이 존재한다(기하를 무시한 근사라
전부 달성 불가능하지만, 순서 개선의 여지가 크다는 신호). 그리고 plan과 실제를 block별로 대조하면
미끄러짐(slip)이 특정 block들에 몰려 있다 — prob_31에서 tardiness의 92~95%가 장기(P≥20) block이고,
tardiness 최대 block들은 plan상 burst 초입(11~46일)에 들어가야 하는데 실제로는 **burst가 끝난
뒤(56~69일, slip +40~55일)**에야 들어간다. window 내내 굶은 것이다.

## 현재 문제

### 1. ATC urgency는 3일 뒤를 모른다

지금 급한 block을 넣는 것이 "3일 뒤 도착할 훨씬 큰 block의 마지막 자리"를 없애는 결정일 수
있는데, 매 event의 국소 urgency는 그걸 볼 수 없다. window 전체를 보는 눈이 없다.

### 2. event-단위 개선은 전부 시도해서 죽었다 (재시도 금지)

* 같은 event 안에서 여러 admission 순서를 시도해 최선을 고르기 → scan 비용 배수, 이득 0
  (event 안은 이미 최적이었다).
* urgency에 면적 항을 넣어 대형 block을 우선하기(α<0) → 짧은 예산(첫 construction) 실험에선
  이겼지만 배포 예산에선 탐색이 그 이점을 지우고 오히려 해가 됐다.
* 결론: 격차는 event **안**이 아니라 event **사이**(window 전체의 배분)에 있고, 그걸 고치려면
  event loop 밖에서 만든 **전역 plan**이 필요하다.

## 개선 방향

### A. plan 층 신설 + urgency가 "plan 대비 지연"을 보게 (plan-guided admission)

**어디를 바꿀까:** 새 파일 `Phase2/plan.py` + [dispatch.py의 urgency 함수 `_prio`](../Phase2/dispatch.py#L76).

**논리:** capacity relaxation plan은 수 밀리초에 풀리고(진단에서 이미 사용 중인 greedy), block별
**목표 admission 일**을 준다. urgency의 slack을 due date 기준이 아니라 **plan 일 기준**으로(또는
혼합으로) 계산하면, plan보다 늦어지는 block의 우선순위가 자동으로 올라간다 — window 전체를 본
결정이 매 event의 국소 결정 안으로 들어온다. admission 자체는 여전히 기존 배치 엔진이
하므로(비선점), 검증된 기하·crane 경로는 그대로다.

**빠른 선행 검증(코드 0줄):** due date를 plan 완료일로 치환한 의사-due 인스턴스로 배치 엔진을
돌리고 진짜 due date로 재채점하면, 이 아이디어의 상한을 구현 없이 잴 수 있다(프로토타입 실험
설계 완료, 결과 대기 중).

### B. plan을 동률 깨기(tiebreak)로만 (보수판)

urgency 점수는 그대로 두고 점수가 비슷한 block끼리의 순서만 plan 순으로. 효과는 작지만 부작용
위험 최소 — A가 과격하다고 판명될 때의 후퇴선.

### C. burst window에서만 활성화

대기 queue가 임계 이상인 event(=window 내부)에서만 plan 항을 적용해, window 밖의 검증된 현행
동작을 보존.

## 우선순위

**A(+C 결합)**. 근거: (1) 격차가 window 안에 있음이 실측으로 확정됐고, (2) plan 계산 로직은 이미
작성·사용 중이라 재사용이며, (3) event-단위 대안은 전부 계측 사망이라 남은 유일한 레버 방향이고,
(4) prob_31 원인 확정(→ [04-small-bay](04-small-bay.md))이 "admission 순서만이 남은 차원"임을
별도로 증명했다. 플래그 게이트 + min-wins 워커 배치로 실패 시 손실 0.

## 기대 효과

* plan상 burst 초입에 들어가야 할 block들의 slip +40~55일 회수 → tardiness의 몸통 직격.
* 저항 문제들(31·39·30·26)의 plan 대비 2.2~3.5× tardiness 갭 축소.
* 배치 엔진·기하·crane 경로 무변경(plan은 우선순위만 만짐) — 검증된 부분을 안 건드리는
  최소침습 구조.
