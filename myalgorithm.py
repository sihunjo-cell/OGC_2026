# myalgorithm.py -- OGC 2026 제출 진입점.
#
# 대회 스켈레톤을 따른 algorithm(prob_info, timelimit) -> dict.
# 코어는 병렬 ALNS 포트폴리오(Outer/portfolio.py): 4코어에서 4개 멤버를 독립
# 서브프로세스로 돌려 best를 취하므로, 결과가 안전 baseline(멤버 0)보다 나빠지지 않는다.


def algorithm(prob_info, timelimit=60):
    """평가 서버가 요구하는 진입점.

    prob_info : 인스턴스 dict.  timelimit : wall-clock 초.
    제출 해 dict {"operations": {...}}를 반환한다.
    """
    # reserve = 부모의 launch/read 오버헤드용 안전 여유분(타임아웃은 전체 실패로 채점).
    # budget = 포트폴리오에 넘기는 실제 예산. reserve는 포트폴리오 내부 reap 마진(<=25s)보다 크게 둔다.
    reserve = min(max(25.0, 0.05 * timelimit), 60.0)
    budget = max(5.0, timelimit - reserve)

    try:
        from Outer.portfolio import optimize_portfolio
        sol = optimize_portfolio(prob_info, budget, n_workers=4)
        if sol is not None:
            return sol
    except Exception:
        pass

    # 폴백(서브프로세스/임포트 실패): 안전 baseline을 in-process로 실행.
    from Phase0 import preprocess
    from Outer import alns
    from Outer.portfolio import default_portfolio
    import time as _time
    t0 = _time.time()
    pre = preprocess(prob_info)
    s, _ = alns(prob_info, pre, max(1.0, budget - (_time.time() - t0) - 3.0),
                default_portfolio()[0])
    return s.solution
