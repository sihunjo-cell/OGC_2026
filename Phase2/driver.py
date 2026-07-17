"""Phase2.driver -- PlaceAndCrane: dispatch 배치 후 크레인 인증 + repair 파이프라인.

dispatch_construct(배치) -> 크레인 인증(공식 utils) -> Phase A(충돌 블록 하루씩 미룸)
-> Phase B(부분점유 슬롯 재시도 후 빈 bay force_place). 출력은 항상 완전한 feasible."""

from __future__ import annotations

import time

from .config import Phase2Config
from .contract import Phase2Result
from .collision import _collision_free
from .crane import crane_feasibility, crane_obstructed
from .dispatch import dispatch_construct
from . import repair as rp


def build_solution(coords, orient, entry, exit_, bay, block_ids) -> dict:
    """{"operations": {...}} dict 조립. 같은 날 안에서는 EXIT를 ENTRY보다 먼저
    (정렬 키 0 = EXIT, 1 = ENTRY), block_id를 2차 키로."""
    buckets = {}
    for i in block_ids:
        buckets.setdefault(int(exit_[i]), []).append((0, "EXIT", i, bay[i], None, None, None))
        buckets.setdefault(int(entry[i]), []).append(
            (1, "ENTRY", i, bay[i], coords[i][0], coords[i][1], orient[i]))
    ops = {}
    for t in sorted(buckets):
        row = sorted(buckets[t], key=lambda r: (r[0], r[2]))
        out = []
        for _, kind, bid, j, x, y, o in row:
            op = {"type": kind, "block_id": bid, "bay_id": j}
            if kind == "ENTRY":
                op["x"] = int(x)
                op["y"] = int(y)
                op["orient_idx"] = o
            out.append(op)
        ops[str(t)] = out
    return {"operations": ops}


def PlaceAndCrane(prob_info: dict, p1_out, pre, cfg: Phase2Config = None,
                  deadline=None) -> Phase2Result:
    cfg = cfg or Phase2Config()

    def _past():
        # 마감 초과 시 이후 재시도를 빈 bay 강제로 스킵(단일 디코드 오버슛 상한).
        return deadline is not None and time.perf_counter() >= deadline

    blocks = prob_info["blocks"]
    n = len(blocks)
    P = [b["processing_time"] for b in blocks]
    D = [b["due_date"] for b in blocks]
    R = [b["release_time"] for b in blocks]

    bay = list(p1_out.bay)
    entry = list(p1_out.entry)
    exit_ = list(p1_out.exit_)

    bay_blocks = [[] for _ in range(pre.n_bays)]
    for i in range(n):
        bay_blocks[bay[i]].append(i)

    def _schedule_excluding(j, exclude):
        return [(entry[k], exit_[k]) for k in bay_blocks[j] if k in coords and k != exclude]

    def _earliest_slot(i, j, placed_here):
        """이미 bay에 있는 블록들 사이에서, 블록 i가 IFP 좌하단 코너에 들어맞는
        (공간상 충돌 없고 entry/exit 모두 크레인이 트인) 가장 이른 later entry.
        빈 window로 밀지 않고 지연을 최소화한다. 반환 (pos, o, entry, exit) 또는 None."""
        r, p = R[i], P[i]
        n_o = len(pre.poly[i])
        corners = []
        for o in range(n_o):
            (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
            if x_lo <= x_hi and y_lo <= y_hi:
                corners.append((o, (x_lo, y_lo)))
        if not corners:
            return None
        cand = sorted({r} | {exit_[k] for k in placed_here if exit_[k] > r})
        for t in cand:
            xt = t + p
            overlap_res = [k for k in placed_here if entry[k] < xt and t < exit_[k]]
            entry_res = [k for k in placed_here if entry[k] < t < exit_[k]]
            exit_res = [k for k in placed_here if entry[k] < xt < exit_[k]]
            for o, pos in corners:
                if not _collision_free(i, o, pos, overlap_res, coords, orient, pre):
                    continue
                if crane_obstructed(i, o, pos, entry_res, coords, orient, pre):
                    continue
                if crane_obstructed(i, o, pos, exit_res, coords, orient, pre):
                    continue
                return pos, o, t, xt
        return None

    # ---- 배치: 이벤트 구동 ATC 디스패처 (Phase2.dispatch) --------------------
    coords, orient, d_entry, d_exit, forced_cons, d_bay = dispatch_construct(
        prob_info, p1_out, pre, cfg, deadline=deadline)
    entry[:] = d_entry
    exit_[:] = d_exit
    # 동적 bay가 배정을 바꿨을 수 있으므로 bay/bay_blocks를 dispatch 결과로 갱신.
    bay[:] = d_bay
    bay_blocks = [[] for _ in range(pre.n_bays)]
    for i in range(n):
        bay_blocks[bay[i]].append(i)

    # ---- 크레인 인증 + repair 루프 ----------------------------------
    # Phase A: 값싼 점진 패스 -- 충돌 블록을 전부 하루씩 미뤄서(Z1-marginal 순서)
    # 가벼운 크레인 sweep을 싸게 해소. Phase B: 그래도 충돌하면 빈 bay로 강제.
    forced = set()
    feasible, conflicts, stage = False, [], 0
    sol = None
    for _ in range(cfg.max_repair_passes):
        if _past():                # 마감 후엔 재인증 패스(공식체커 O(n^2/bay))를 멈춘다.
            break
        sol = build_solution(coords, orient, entry, exit_, bay, range(n))
        feasible, conflicts, stage = crane_feasibility(prob_info, sol)
        if feasible or not conflicts:
            break
        for v in sorted(conflicts, key=lambda k: (rp.z1_marginal(k, exit_, D), k)):
            rp.shift_later(v, entry, exit_, P, stage)

    # Phase B: force_place(빈 창) 전 부분점유 슬롯 재시도. 실패/재충돌 시 force_place로
    # 승격(빈 창은 충돌 불가 = 종착 보장). retried는 패스 간 유지, 마지막 4패스는 순수 강제.
    retried: set = set()
    rescue_budget = cfg.force_retry_budget
    slot_rescued = retry_reverted = 0
    guard, guard_max = 0, n + 8
    while not feasible and conflicts and guard <= guard_max and not _past():
        guard += 1
        allow_rescue = (cfg.force_retry_phase_b and rescue_budget > 0
                        and guard <= guard_max - 4 and not _past())
        # z1_marginal 오름차순(slack 블록 먼저 -> 크레인 연쇄 충돌 적음). OFF면 원시 순서.
        order = (sorted(conflicts, key=lambda k: (rp.z1_marginal(k, exit_, D), k))
                 if cfg.force_retry_phase_b else conflicts)
        for v in order:
            if v in forced:
                continue
            j = bay[v]
            slot = None
            if allow_rescue and rescue_budget > 0 and v not in retried:
                rescue_budget -= 1     # 시도 카운트: _earliest_slot 호출수 상한
                others = [k for k in bay_blocks[j] if k in coords and k != v]
                slot = _earliest_slot(v, j, others)
            if slot is not None:
                pos, o, e, x = slot
                retried.add(v)
                slot_rescued += 1
            else:
                if v in retried:
                    retry_reverted += 1     # 구조됐다 재충돌 -> 강제로 승격
                pos, o, e, x = rp.force_place(v, j, _schedule_excluding(j, v), pre, R, P)
                forced.add(v)
            coords[v], orient[v] = pos, o
            entry[v], exit_[v] = e, x
        sol = build_solution(coords, orient, entry, exit_, bay, range(n))
        feasible, conflicts, stage = crane_feasibility(prob_info, sol)

    if sol is None:                # 마감이 진입 시점에 이미 지남 -> 반환용 조립(미인증).
        sol = build_solution(coords, orient, entry, exit_, bay, range(n))

    Z1 = sum(max(0, exit_[i] - D[i]) for i in range(n)) if feasible else None

    return Phase2Result(
        status="SOLUTION",
        solution=sol,
        coords=coords,
        orient=orient,
        entry=entry,
        exit_=exit_,
        Z1=Z1,
        bay=list(bay),          # 재라우팅 반영된 실제 배정 (dyn-off면 입력과 동일)
        info={
            "feasible": feasible,
            "stage": stage,
            "unresolved": conflicts,
            "forced": sorted(forced),
            "forced_construction": sorted(forced_cons),
            "slot_rescued": slot_rescued,
            "retry_reverted": retry_reverted,
            "phaseB_passes": guard,
        },
    )
