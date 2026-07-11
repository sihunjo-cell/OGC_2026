# Scoring Sweep File Map

이 문서는 scoring profile 실험 경로에서 각 파일이 맡는 역할만 빠르게 보려는 용도다.

## Core Phase2

- `Phase2/scoring_profiles.py`
  - 실험용 scoring profile 정의 모음.
  - 기본 profile 이름, 비교용 portfolio 목록, profile lookup helper를 제공한다.

- `Phase2/config.py`
  - `Phase2Config` 정의.
  - `scoring_profile`과 명시 override를 합쳐 최종 scoring parameter를 resolve한다.

- `Phase2/scoring.py`
  - placement score 계산식 본체.
  - contact, corner, temporal, premarsh, forced-risk 항을 읽고 resolved config 값으로 최종 점수를 만든다.

- `Phase2/driver.py`
  - `PlaceAndCrane` 메인 entry.
  - profile-resolved config를 사용해 배치/repair를 수행하고 `scoring_profile`, `scoring_params`, `scoring_metrics`를 결과 info에 넣는다.

## Outer / ALNS

- `Outer/realize.py`
  - 고정 bay assignment를 완전한 해로 decode한다.
  - 필요하면 top-k/profile variant를 여러 개 실행하고 최종 objective 기준으로 선택한다.

- `Outer/alns.py`
  - ALNS 반복 루프.
  - 최종 best solution에 iteration 기반 metrics를 다시 얹어 준다.

- `Outer/portfolio.py`
  - 기본 병렬 portfolio 정의.
  - 환경변수 `OGC_SCORING_PROFILE` 또는 명시 인자로 portfolio 전체 Phase2 scoring profile을 바꿀 수 있다.

## Reporting / Automation

- `perf_report.py`
  - 문제 묶음 전체에 대해 성능/시간제한 진단 리포트를 만든다.
  - `--scoring-profile` 인자로 report 전체 실행에 같은 Phase2 scoring profile을 강제로 적용할 수 있다.

- `scripts/compare_scoring_profiles.py`
  - 단일 인스턴스에서 여러 scoring profile을 빠르게 비교하는 스크립트.

- `scripts/run_scoring_profile_sweep.py`
  - 장시간 배치 실행 스크립트.
  - 여러 scoring profile에 대해 `perf_report.py`를 순차 실행하고 로그, CSV, README를 남긴다.

## Solver Entry

- `myalgorithm.py`
  - 제출 entry point.
  - 일반 실행 시 `Outer.portfolio.optimize_portfolio()`를 호출한다.
  - `perf_report.py --mode real` 경로에서도 결국 이 파일이 실행된다.
