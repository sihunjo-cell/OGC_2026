# Issue 목록

현 구조에서 남은 미해결 문제. 전부 자체 계측·해부에서 도출했다(6문제 인스턴스 해부 + prob_31
전면 포렌식 + 짧은 timelimit 스모크).

| # | 파일 | 문제 한 줄 | 타깃 | 기대 크기 |
|---|---|---|---|---|
| 01 | [01-burst-plan](01-burst-plan.md) | burst window 안의 admission 순서를 국소 urgency로만 결정 | 31·39·30 (+26) | 대 — plan 대비 slip +40~55일 회수 |
| 02 | [02-big-block](02-big-block.md) | 소형 block이 대형 block의 마지막 연속 free space를 잠식 (tardiness의 87~97%=대형) | 26 (+30·28) | 중 — 26의 유일 대응 |
| 03 | [03-short-budget](03-short-budget.md) | 첫 construction이 잘리면 force_place로 점수 14배 벼랑 | worst-case 보험 | 안정성 |
| 04 | [04-small-bay](04-small-bay.md) | 작은 bay × 장기 block 상성 — prob_31 원인 확정(진단 완료) | 31 + 동일 유형 | 01의 근거·타깃 지정 |

권장 착수 순서: **01(주 처방, 04가 근거) → 02(26 전용, 병행 가능) → 03(보험)**.

검증 규율(전 이슈 공통): 플래그 게이트(off = 현행과 byte 동일) · 부하-쌍대 A/B(비교 대상을 동시
실행해 경합 조건을 맞춤) · 배포 예산(300초+)에서 판정(짧은 예산 결과는 첫 construction 테스트일
뿐) · min-wins 포트폴리오로 무회귀 배포.
