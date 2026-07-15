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

**빠른 선행 검증(코드 0줄) — 결과:** due date를 plan 완료일로 치환한 의사-due 인스턴스로 배치
엔진+탐색 전체를 돌리고 진짜 due date로 재채점했다. prob_31에서 −1.2~2.0%(양 워커 config 일관),
prob_28에서는 +6% 해 — 방향은 맞지만 seed 분산(±3~8%) 수준이라 **치환 방식 그대로는 약하다**.
대신 사후해부가 더 정밀한 주입 지점을 알려줬다(아래 A′).

### A′. plan-triggered reroute — 주입 지점 정밀화 (본 이슈의 현재 1순위)

**어디를 바꿀까:** [dispatch.py의 dynamic bay 발동 조건](../Phase2/dispatch.py#L193) 한 줄.

**논리:** tardiness 최대 block들을 사후해부하니 **12/12가 자기 plan 일에 다른 bay에 면적상 자리가
있었다**(예: 필요 159 vs 빈 1214). 못 쓴 이유는 dynamic bay의 발동 조건이 `t+P>D`(이미 늦음이
확정된 뒤)라서다 — 장기 block은 확정적으로 늦어질 때까지 다른 bay를 쓸 자격이 없고, 기다리는 사이
burst가 자리를 다 삼킨다. 조건을 **"자기 bay에 자리 없음 + plan 일 경과"**로 바꾸면(상수 0개),
plan이 "이미 들어갔어야 한다"고 말하는 block이 즉시 다른 bay를 쓸 수 있다. due 치환(위 프로토)과
달리 urgency 체계는 건드리지 않고 reroute 자격만 앞당긴다.

### B. plan을 동률 깨기(tiebreak)로만 (보수판)

urgency 점수는 그대로 두고 점수가 비슷한 block끼리의 순서만 plan 순으로. 효과는 작지만 부작용
위험 최소 — A가 과격하다고 판명될 때의 후퇴선.

### C. burst window에서만 활성화

대기 queue가 임계 이상인 event(=window 내부)에서만 plan 항을 적용해, window 밖의 검증된 현행
동작을 보존.

## 우선순위

**A′(plan-triggered reroute) 먼저, 그다음 A(+C)**. 근거: (1) A′는 사후해부의 직접 증거(12/12가
plan 일에 자리 존재 + 발동 조건이 대기를 강제)를 한 줄 수정으로 겨냥하고, (2) 의사-due 프로토가
"plan 신호는 유효하나 urgency 치환은 과격"임을 보였으며(31 −1.2~2.0%, 28 +6% 해 = per-problem),
(3) plan 계산 로직은 이미 작성·사용 중이라 재사용이고, (4) event-단위 대안은 전부 계측 사망이라
남은 유일한 레버 방향이다. 플래그 게이트 + min-wins 워커 배치로 실패 시 손실 0. per-problem
성격이 확인됐으므로 배포는 blanket이 아니라 **워커 스코프**(min-wins)로.

## 기대 효과

* plan상 burst 초입에 들어가야 할 block들의 slip +40~55일 회수 → tardiness의 몸통 직격.
* 저항 문제들(31·39·30·26)의 plan 대비 2.2~3.5× tardiness 갭 축소.
* 배치 엔진·기하·crane 경로 무변경(plan은 우선순위만 만짐) — 검증된 부분을 안 건드리는
  최소침습 구조.
