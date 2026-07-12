"""Phase2.driver -- PlaceAndCrane (최소 경로, OUTER 없음).

bay별 -> clique별 -> resident_sets -> 극대 시점별로: 미배치 resident를 정렬해
place_block(충돌 검사, 필요시 크레인 게이트)한다. 실패하면 빈 bay safety net으로
폴백. 이후 2.6 improve(기본 off) -> 크레인 인증 -> repair 루프 -> 실제 Z1."""

from __future__ import annotations

from .config import Phase2Config
from .contract import Phase2Result
from .residents import resident_sets
from . import ordering
from .collision import place_block, _collision_free
from .crane import crane_feasibility, crane_obstructed
from .improve import improve_layout, ImproveCtx
from . import repair as rp


def _overlap(entry, exit_, a, b) -> bool:
    """반열린 구간 겹침 [entry_a, exit_a) & [entry_b, exit_b)."""
    return entry[a] < exit_[b] and entry[b] < exit_[a]


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


def PlaceAndCrane(prob_info: dict, p1_out, pre, cfg: Phase2Config = None) -> Phase2Result:
    cfg = cfg or Phase2Config()
    cfg.resolve_scoring_profile()
    if getattr(cfg, "_debug_variant_stats", None) is None:
        cfg._debug_variant_stats = {}
    blocks = prob_info["blocks"]
    n = len(blocks)
    P = [b["processing_time"] for b in blocks]
    D = [b["due_date"] for b in blocks]
    R = [b["release_time"] for b in blocks]

    bay = list(p1_out.bay)
    entry = list(p1_out.entry)
    exit_ = list(p1_out.exit_)
    coords: dict = {}
    orient: dict = {}

    bay_blocks = [[] for _ in range(pre.n_bays)]
    for i in range(n):
        bay_blocks[bay[i]].append(i)

    def _schedule_excluding(j, exclude):
        return [(entry[k], exit_[k]) for k in bay_blocks[j] if k in coords and k != exclude]

    def _commit(i, j, pos, o, placed_here):
        coords[i] = pos
        orient[i] = o
        placed_here.append(i)

    def _earliest_slot(i, j, placed_here):
        """이미 bay에 있는 블록들 사이에서, 블록 i가 IFP 좌하단 코너에 들어맞는
        (공간상 충돌 없고 entry/exit 모두 크레인이 트인) 가장 이른 later entry.
        완전 빈 window로 밀지 않고 지연(= 지연도)을 최소화한다. 반환 (pos, o,
        entry, exit) 또는 None."""
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

    def _place_one(i, j, placed_here, G):
        residents = [k for k in placed_here if _overlap(entry, exit_, i, k)]
        best = place_block(i, j, residents, coords, orient, entry, exit_,
                           prob_info, pre, cfg, m=len(placed_here), G=G)
        if best is not None:
            _commit(i, j, best.pos, best.o, placed_here)
            return
        # 자기 시간에 못 놓음 -> 가장 이른 later feasible 슬롯 선택
        slot = _earliest_slot(i, j, placed_here) if cfg.forcing_mode == "earliest_slot" else None
        if slot is not None:
            pos, o, e, x = slot
            entry[i], exit_[i] = e, x
            _commit(i, j, pos, o, placed_here)
        else:
            # 최후 수단: 빈 bay window + IFP 좌하단 코너
            pos, o, e, x = rp.force_place(i, j, _schedule_excluding(j, i), pre, R, P)
            entry[i], exit_[i] = e, x
            _commit(i, j, pos, o, placed_here)

    # ---- 배치, bay 하나씩 ----------------------------------------------
    for j in range(pre.n_bays):
        G = len(bay_blocks[j])
        placed_here: list = []
        cliques = p1_out.cliques[j] if j < len(p1_out.cliques) else []
        cliques = sorted(cliques, key=lambda C: min(entry[i] for i in C)) if cliques else []

        for C in cliques:
            SB, TT = resident_sets(C, entry, exit_)
            for t in TT:
                unplaced = [i for i in SB[t] if i not in coords]
                for i in ordering.static_order(unplaced, pre, cfg):
                    if i not in coords:
                        _place_one(i, j, placed_here, G)
        # clique에 안 잡힌 bay 블록 (방어적; singleton으로 전부 커버돼야 정상)
        for i in bay_blocks[j]:
            if i not in coords:
                _place_one(i, j, placed_here, G)

    # ---- 2.6 개선 (기본 off) --------------------------------------
    ictx = ImproveCtx(pre=pre, cfg=cfg, prob_info=prob_info, bay=bay,
                      entry=entry, exit_=exit_, bay_blocks=bay_blocks)
    coords, orient = improve_layout(coords, orient, ictx)

    # ---- 크레인 인증 + repair 루프 ----------------------------------
    # Phase A: 값싼 점진 패스 몇 번 -- 충돌 블록을 전부 하루씩 미뤄서(Z1-marginal
    # 순서) 가벼운 크레인 sweep을 싸게 해소.
    # Phase B (아래): 그래도 충돌하는 블록은 빈 bay window로 강제.
    forced = set()
    feasible, conflicts, stage = False, [], 0
    for _ in range(cfg.max_repair_passes):
        sol = build_solution(coords, orient, entry, exit_, bay, range(n))
        feasible, conflicts, stage = crane_feasibility(prob_info, sol)
        if feasible or not conflicts:
            break
        for v in sorted(conflicts, key=lambda k: (rp.z1_marginal(k, exit_, D), k)):
            rp.shift_later(v, entry, exit_, P, stage)

    # Phase B: 대량 safety net. 강제된 블록마다 다른 블록이 하나도 없는 window를
    # 주므로 다시 충돌하지 않는다. check_feasibility가 첫 실패 stage만 보고하므로
    # 루프. 매 패스마다 새 블록이 최소 1개 강제되니 종료.
    guard = 0
    while not feasible and conflicts and guard <= n:
        guard += 1
        for v in conflicts:
            if v in forced:
                continue
            j = bay[v]
            pos, o, e, x = rp.force_place(v, j, _schedule_excluding(j, v), pre, R, P)
            coords[v], orient[v] = pos, o
            entry[v], exit_[v] = e, x
            forced.add(v)
        sol = build_solution(coords, orient, entry, exit_, bay, range(n))
        feasible, conflicts, stage = crane_feasibility(prob_info, sol)

    Z1 = sum(max(0, exit_[i] - D[i]) for i in range(n)) if feasible else None
    scoring_stats = dict(getattr(cfg, "_debug_variant_stats", {}))
    for key in (
        "candidate_evaluations",
        "feasible_candidates_total",
        "exact_candidates_total",
        "forced_risk_evaluations",
        "forced_risk_fast_evaluations",
        "forced_risk_exact_evaluations",
        "forced_risk_resident_checks",
        "forced_risk_crane_checks",
        "selected_placements",
        "selected_forced_risk_total",
    ):
        scoring_stats.setdefault(key, 0)
    selected = scoring_stats.get("selected_placements", 0)
    scoring_stats["selected_forced_risk_avg"] = (
        scoring_stats.get("selected_forced_risk_total", 0.0) / selected
        if selected else None
    )
    scoring_stats["candidate_evals"] = scoring_stats.get("candidate_evaluations", 0)
    scoring_stats["forced_risk_evals"] = scoring_stats.get("forced_risk_evaluations", 0)
    scoring_stats["avg_selected_forced_risk"] = scoring_stats.get("selected_forced_risk_avg")
    scoring_stats.update({
        "profile": cfg.scoring_profile,
        "objective": None,
        "Z1": Z1,
        "forced": len(forced),
        "realize_ms": None,
        "iters": None,
        "iters_per_sec": None,
        "ms_per_realize": None,
    })

    return Phase2Result(
        status="SOLUTION",
        solution=sol,
        coords=coords,
        orient=orient,
        entry=entry,
        exit_=exit_,
        Z1=Z1,
        info={
            "feasible": feasible,
            "stage": stage,
            "unresolved": conflicts,
            "forced": sorted(forced),
            "scoring_profile": cfg.scoring_profile,
            "scoring_params": cfg.scoring_params(),
            "scoring_metrics": scoring_stats,
        },
    )
