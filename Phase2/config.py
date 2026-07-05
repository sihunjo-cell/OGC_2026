"""Phase2.config -- 선택된 배치 설정의 튜닝 노브.
`improve_mode`와 `forcing_mode`가 병렬 포트폴리오에서 바꾸는 두 레버."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Phase2Config:
    # -- 포트폴리오 레버 (멤버마다 다르게 설정) -------------------
    improve_mode: str = "off"           # 2.6  : "off" | "jostle_2exchange"
    forcing_mode: str = "empty_bay"     # 2.5  : "empty_bay" | "earliest_slot"
    #   earliest_slot = 늦춰진 블록을 resident 사이의 가장 이른 later-feasible
    #   window에 강제 (완전 빈 window 대신). 분산이 커서 기본값 아니라
    #   포트폴리오 멤버로만 둔다.

    # -- 2.1 배치 순서 --------------------------------------------------
    order_mode: str = "area"            # "area" (어렵고 큰 것 먼저) | "mst"
    #   mst = 최소 slack 우선 (납기 반영): 급한 블록(slack = due - release -
    #   processing 이 작은 것)이 non-forced 슬롯을 먼저 차지해 지연 감소.
    #   기본값 아니라 포트폴리오 멤버.
    lam1: float = 1.0                   # area 난이도 가중치 (어려운 것 먼저)

    # -- 2.3 배치 점수 (CONTACT: 가중 혼합 + S-curve) --------------
    w_ct: float = 1.0                   # contact 가중치
    w_cn: float = 0.01                  # corner 가중치 (tie-break 스케일)
    w_tp: float = 0.1                   # temporal (EXIT 근접) 가중치
    w_pm: float = 1.0                   # premarshalling 가중치
    K: float = 4.0                      # S-curve 기울기 (초반 contact -> 후반 corner)

    # -- 2.6 개선 ------------------------------------------------------
    improve_rounds: int = 2             # improve 호출당 Jostle sweep 횟수 (improve_mode on)

    # -- 2.5 repair -----------------------------------------------------------
    max_repair_passes: int = 2          # safety net 전에 도는 점진적 크레인 repair 패스 수
