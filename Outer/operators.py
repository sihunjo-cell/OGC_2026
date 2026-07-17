"""적응적 연산자 선택(AOS): 룰렛 선택 + 세그먼트 가중치 갱신."""

from __future__ import annotations

import hashlib
from collections import defaultdict


def _vkey(sol):
    """방문집합 키 = Solution.key()의 16B digest (메모리 상수화)."""
    return hashlib.blake2b(repr(sol.key()).encode(), digest_size=16).digest()


class AOS:
    def __init__(self, destroy_ops, repair_ops, cfg):
        self.cfg = cfg
        self.dops = list(destroy_ops)
        self.iops = list(repair_ops)
        self.W_rem = {o: 1.0 for o in self.dops}
        self.W_ins = {o: 1.0 for o in self.iops}
        self._reset_tallies()
        self.visited = set()

    def mark_visited(self, sol):
        self.visited.add(_vkey(sol))

    def _reset_tallies(self):
        self.scores_rem = defaultdict(float)
        self.scores_ins = defaultdict(float)
        self.attempts_rem = defaultdict(int)
        self.attempts_ins = defaultdict(int)

    @staticmethod
    def _roulette(weights, rng):
        total = sum(weights.values())
        if total <= 0:
            return rng.choice(list(weights))
        threshold = rng.random() * total
        acc = 0.0
        for op, w in weights.items():
            acc += w
            if threshold <= acc:
                return op
        return next(reversed(weights))

    def select(self, rng):
        op_rem = self._roulette(self.W_rem, rng)
        op_ins = self._roulette(self.W_ins, rng)
        self.attempts_rem[op_rem] += 1
        self.attempts_ins[op_ins] += 1
        return op_rem, op_ins

    def score_update(self, s_new, s_cur, s_best, op_rem, op_ins, accepted):
        key = _vkey(s_new)
        if key in self.visited:
            return
        self.visited.add(key)
        cfg = self.cfg
        if s_new.objective < s_best.objective:
            delta = cfg.sigma1
        elif s_new.objective < s_cur.objective:
            delta = cfg.sigma2
        elif accepted:
            delta = cfg.sigma3
        else:
            delta = 0.0
        self.scores_rem[op_rem] += delta
        self.scores_ins[op_ins] += delta

    def update_weights(self):
        r = self.cfg.r
        for o in self.dops:
            self.W_rem[o] = self.W_rem[o] * (1 - r) + r * (self.scores_rem[o] / max(self.attempts_rem[o], 1))
        for o in self.iops:
            self.W_ins[o] = self.W_ins[o] * (1 - r) + r * (self.scores_ins[o] / max(self.attempts_ins[o], 1))
        self._reset_tallies()
