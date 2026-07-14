# Phase2/_diag.py
"""헛측량(wasted re-scan) 원인 계측 프로브. OGC_DIAG=1 일 때만 활성(그 외엔 무부하).

raster.scan / raster._stamp / dispatch._try_admit 에 훅을 걸어 '스캔이 재계산됐는데
블록은 안 들어간' 사건을 두 축으로 분해한다.
  축1 재계산 원인: cold(첫 스캔) / intra_add / intra_exit(같은 tick) / inter_tick.
  축2 이벤트 결과: admitted(헛측량 아님) / no_space(앵커 0) / gate_fail(게이트 탈락).
cost = einsum FLOP 프록시 = Σ_{활성층 k} R*C*MH*MW. 비용가중 귀속에 사용."""

from __future__ import annotations

import os
from collections import defaultdict


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() not in ("", "0", "false", "False")


class _Probe:
    def __init__(self):
        self.enabled = _flag("OGC_DIAG")
        self.reset()

    def reset(self):
        self.tick = None
        self.phase = None                 # 'exit' | 'admit'
        # stamp_log[j][m] = (tick, phase, sgn, block) : ver 를 m -> m+1 로 올린 스탬프
        self.stamp_log = defaultdict(list)
        # 블록별 스캔 집계
        self.blk_scan = defaultdict(lambda: {"calls": 0, "miss": 0, "cost": 0.0})
        # 블록별 admission 시도 집계
        self.blk_att = defaultdict(lambda: {
            "n": 0, "no_space": 0, "gate_fail": 0, "admitted": 0,
            "admit_tick": None, "cells_tried": 0, "gate_rej": 0})
        # 원인별 미스 집계 (비용가중)
        self.cause = defaultdict(lambda: {"calls": 0, "cost": 0.0})
        # 원인 × 결과 교차 (헛측량 귀속의 핵심 표)
        #   결과는 그 스캔을 낸 블록의 '이번 이벤트' 최종결과로 사후 조인 불가하므로,
        #   스캔 시점엔 원인만 알고 결과는 attempt에서 따로 집계 -> finalize에서 블록조인.
        self.miss_by_cause_tickblk = defaultdict(lambda: {"calls": 0, "cost": 0.0})
        # (tick, block) -> 원인별 미스비용  : attempt 결과와 조인해 헛측량 원인 귀속
        self.staleness = defaultdict(int)   # (v1 - v0) 히스토그램
        self.n_scan_calls = 0
        self.n_scan_miss = 0
        self.n_scan_hit = 0
        # attempt raw (상위 오프렌더 추출용, 안전 상한)
        self.attempts = []
        self._att_cap = 200000
        # 재계산이 '정말 필요했나' -- 새 스캔지도 vs 버린(캐시) 스캔지도의 차이
        self.diff = {"recompute": 0, "identical": 0,
                     "changed_cells": 0, "total_cells": 0}
        self.diff_hist = defaultdict(int)     # 바뀐 칸 비율 버킷 -> 재계산 수
        # 작업장 통째로 다시 만드는 캐시들(점유 합집합/접촉장) 재빌드 비용
        self.wholebay = defaultdict(lambda: {"count": 0, "cost": 0.0})
        # 스탬프 하나가 실제로 더럽히는 영역(블록 발자국) vs 작업장 넓이
        self.foot = {"n": 0, "foot_sum": 0, "bay_sum": 0}

    # -- dispatch 컨텍스트 --------------------------------------------------
    def set_ctx(self, tick, phase):
        if not self.enabled:
            return
        self.tick = tick
        self.phase = phase

    # -- raster.scan 훅 -----------------------------------------------------
    def on_scan_hit(self, j, i, o):
        if not self.enabled:
            return
        self.n_scan_calls += 1
        self.n_scan_hit += 1
        self.blk_scan[i]["calls"] += 1

    def on_scan_miss(self, j, i, o, cached_ver, cur_ver, cold, cost, n_feas):
        if not self.enabled:
            return
        self.n_scan_calls += 1
        self.n_scan_miss += 1
        s = self.blk_scan[i]
        s["calls"] += 1
        s["miss"] += 1
        s["cost"] += cost
        self.staleness[cur_ver - cached_ver] += 1

        cause = self._classify(j, cached_ver, cur_ver, cold)
        c = self.cause[cause]
        c["calls"] += 1
        c["cost"] += cost
        key = (self.tick, i)
        m = self.miss_by_cause_tickblk[key]
        m["calls"] += 1
        m["cost"] += cost
        # 원인 분포도 (tick,block) 단위로 저장해 헛측량 사후귀속
        m.setdefault("by_cause", defaultdict(lambda: {"calls": 0, "cost": 0.0}))
        bc = m["by_cause"][cause]
        bc["calls"] += 1
        bc["cost"] += cost

    def _classify(self, j, v0, v1, cold):
        if cold:
            return "cold"
        interv = self.stamp_log[j][v0:v1]
        if not interv:
            return "cold"           # 캐시된 ver 이후 스탬프 없음(이론상 미스 아님)
        cur = self.tick
        intra_add = any(tk == cur and sg > 0 for (tk, ph, sg, bk) in interv)
        intra_exit = any(tk == cur and sg < 0 for (tk, ph, sg, bk) in interv)
        inter = any(tk != cur for (tk, ph, sg, bk) in interv)
        if intra_add:
            return "intra_add"
        if intra_exit:
            return "intra_exit"
        if inter:
            return "inter_tick"
        return "other"

    # -- 재계산 결과 vs 버린 캐시 지도의 차이 (raster.scan 훅) --------------
    def on_scan_diff(self, changed, total):
        if not self.enabled:
            return
        self.diff["recompute"] += 1
        self.diff["changed_cells"] += changed
        self.diff["total_cells"] += total
        if changed == 0:
            self.diff["identical"] += 1
        f = (changed / total) if total else 0.0
        if f == 0.0:
            b = "0%(동일)"
        elif f <= 0.01:
            b = "0-1%"
        elif f <= 0.05:
            b = "1-5%"
        elif f <= 0.20:
            b = "5-20%"
        elif f <= 0.50:
            b = "20-50%"
        else:
            b = "50-100%"
        self.diff_hist[b] += 1

    # -- 작업장 통째 캐시 재빌드 (raster.union_ge/contact_field 훅) --
    def on_wholebay(self, kind, cost):
        if not self.enabled:
            return
        w = self.wholebay[kind]
        w["count"] += 1
        w["cost"] += cost

    # -- raster._stamp 훅 ---------------------------------------------------
    def on_stamp(self, j, i, sgn, foot=0, bay=0):
        if not self.enabled:
            return
        self.stamp_log[j].append((self.tick, self.phase, sgn, i))
        self.foot["n"] += 1
        self.foot["foot_sum"] += foot
        self.foot["bay_sum"] += bay

    # -- dispatch._try_admit 훅 --------------------------------------------
    def on_attempt(self, tick, j, i, rank, earlier, outcome,
                   feas_anchors, cells_tried, gate_rej):
        if not self.enabled:
            return
        a = self.blk_att[i]
        a["n"] += 1
        a[outcome] += 1
        a["cells_tried"] += cells_tried
        a["gate_rej"] += gate_rej
        if outcome == "admitted":
            a["admit_tick"] = tick
        if len(self.attempts) < self._att_cap:
            self.attempts.append({
                "tick": tick, "bay": j, "blk": i, "rank": rank,
                "earlier_admits": earlier, "outcome": outcome,
                "feas_anchors": feas_anchors, "cells_tried": cells_tried,
                "gate_rej": gate_rej,
            })

    # -- 집계 ---------------------------------------------------------------
    def finalize(self, prob=None, meta=None):
        # 블록별 최종 결과 (construct 내에서 admit 됐나)
        admitted_blk = {i for i, a in self.blk_att.items() if a["admitted"] > 0}
        # 헛측량 원인귀속: (tick,block)의 미스비용을 그 블록의 '이번 tick 결과'로 태깅.
        #   결과는 attempt raw 에서 (tick,block)->outcome 로 역인덱스.
        outcome_at = {}
        for a in self.attempts:
            outcome_at[(a["tick"], a["blk"])] = a["outcome"]
        waste = defaultdict(lambda: {"calls": 0, "cost": 0.0})   # cause -> 헛측량(비admit)
        useful = defaultdict(lambda: {"calls": 0, "cost": 0.0})  # cause -> admit로 이어진 스캔
        for (tk, blk), m in self.miss_by_cause_tickblk.items():
            oc = outcome_at.get((tk, blk))
            bucket = useful if oc == "admitted" else waste
            for cause, bc in m.get("by_cause", {}).items():
                bucket[cause]["calls"] += bc["calls"]
                bucket[cause]["cost"] += bc["cost"]

        def _tot(d):
            return {"calls": sum(v["calls"] for v in d.values()),
                    "cost": sum(v["cost"] for v in d.values())}

        # 상위 오프렌더: 재계산 비용 큰데 admit 안 된 블록
        offenders = sorted(
            ((i, self.blk_scan[i]["cost"], self.blk_scan[i]["miss"],
              self.blk_att[i]["n"], i in admitted_blk,
              self.blk_att[i]["gate_rej"])
             for i in self.blk_scan),
            key=lambda r: -r[1])[:25]

        out = {
            "prob": prob,
            "meta": meta or {},
            "totals": {
                "scan_calls": self.n_scan_calls,
                "scan_hit": self.n_scan_hit,
                "scan_miss": self.n_scan_miss,
                "hit_rate": (self.n_scan_hit / self.n_scan_calls) if self.n_scan_calls else 0.0,
                "n_blocks_admitted_construct": len(admitted_blk),
            },
            # 축1: 재계산 원인 (전체 미스)
            "miss_by_cause": {k: v for k, v in sorted(
                self.cause.items(), key=lambda kv: -kv[1]["cost"])},
            # 축1 × 축2: 헛측량(비admit) vs 유용(admit) 의 원인별 비용
            "wasted_by_cause": {k: v for k, v in sorted(
                waste.items(), key=lambda kv: -kv[1]["cost"])},
            "useful_by_cause": {k: v for k, v in sorted(
                useful.items(), key=lambda kv: -kv[1]["cost"])},
            "wasted_total": _tot(waste),
            "useful_total": _tot(useful),
            # 축2: attempt 결과 분포
            "attempt_outcomes": {
                "total": sum(a["n"] for a in self.blk_att.values()),
                "admitted": sum(a["admitted"] for a in self.blk_att.values()),
                "no_space": sum(a["no_space"] for a in self.blk_att.values()),
                "gate_fail": sum(a["gate_fail"] for a in self.blk_att.values()),
                "gate_rej_cells": sum(a["gate_rej"] for a in self.blk_att.values()),
            },
            "staleness_hist": dict(sorted(self.staleness.items())),
            # 인과 증거: 재계산 결과가 버린 캐시와 얼마나 달랐나
            "recompute_redundancy": {
                "recompute_noncold": self.diff["recompute"],
                "identical": self.diff["identical"],
                "identical_share": (self.diff["identical"] / self.diff["recompute"])
                                   if self.diff["recompute"] else 0.0,
                "changed_cells": self.diff["changed_cells"],
                "total_cells": self.diff["total_cells"],
                "mean_changed_fraction": (self.diff["changed_cells"] / self.diff["total_cells"])
                                         if self.diff["total_cells"] else 0.0,
                "hist": dict(self.diff_hist),
            },
            # 스탬프 하나가 더럽히는 영역 vs 작업장 (과잉 무효화 배율)
            "stamp_footprint": {
                "n": self.foot["n"],
                "avg_foot": (self.foot["foot_sum"] / self.foot["n"]) if self.foot["n"] else 0.0,
                "avg_bay": (self.foot["bay_sum"] / self.foot["n"]) if self.foot["n"] else 0.0,
                "avg_ratio": (self.foot["foot_sum"] / self.foot["bay_sum"])
                             if self.foot["bay_sum"] else 0.0,
            },
            # 작업장 통째 재빌드 캐시들의 비용 (einsum 스캔 외 추가 whole-bay 비용)
            "wholebay_rebuilds": {k: v for k, v in sorted(
                self.wholebay.items(), key=lambda kv: -kv[1]["cost"])},
            "top_offenders": [
                {"blk": i, "scan_cost": c, "scan_miss": mi, "attempts": na,
                 "admitted": adm, "gate_rej": gr}
                for (i, c, mi, na, adm, gr) in offenders],
        }
        return out


PROBE = _Probe()
