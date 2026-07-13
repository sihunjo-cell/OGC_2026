import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Phase0 import preprocess                      # noqa: E402
from Phase1 import BuildBayAssignment              # noqa: E402
from Phase2 import Phase2Config                    # noqa: E402
from Phase2.crane import _load_utils               # noqa: E402
from Outer.realize import realize                  # noqa: E402


def _decode(name, mode):
    prob_info = json.load(open(ROOT / "train" / f"{name}.json", encoding="utf-8"))
    pre = preprocess(prob_info)
    p1 = BuildBayAssignment(prob_info, pre, None)
    cfg = Phase2Config(construction_mode=mode)
    # realize의 variant 로터리를 끄고 단일 디코드로 비교 (topks를 기본 top_k 하나로)
    cfg.phase2_variant_topks = ()
    s = realize(p1.bay, prob_info, pre, cfg)
    return prob_info, s


def test_dispatch_feasible_and_certified():
    """디스패처 디코드가 공식 체커를 통과해야 한다 (soundness 최종 관문)."""
    utils = _load_utils()
    for name in ("prob_1", "prob_27"):
        prob_info, s = _decode(name, "dispatch")
        chk = utils.check_feasibility(prob_info, s.solution)
        assert chk["feasible"], (name, chk.get("violations", [])[:3])


def test_dispatch_vs_legacy_reported():
    """비교 수치 출력(정보용) + 디스패처가 완전한 배정을 내는지 확인."""
    for name in ("prob_1", "prob_27"):
        prob_info, s_leg = _decode(name, "clique")
        _, s_dis = _decode(name, "dispatch")
        n = len(prob_info["blocks"])
        assert len(s_dis.coords) == n
        print(f"{name}: legacy obj={s_leg.objective:.0f} forced={s_leg.forced} | "
              f"dispatch obj={s_dis.objective:.0f} forced={s_dis.forced}")


if __name__ == "__main__":
    test_dispatch_feasible_and_certified()
    test_dispatch_vs_legacy_reported()
    print("OK")
