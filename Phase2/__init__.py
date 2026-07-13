"""Phase2 -- OGC 2026 블록 배치 + 크레인 실현가능성 솔버.
PlaceAndCrane가 이벤트 구동 ATC 디스패처(dispatch_construct)로 배치한 뒤
크레인 인증 -> repair 파이프라인을 돈다. 단계별 상세는 각 모듈 참고."""

from .config import Phase2Config
from .contract import Phase1Output, Phase2Result
from .driver import PlaceAndCrane, build_solution

__all__ = [
    "Phase2Config",
    "Phase1Output", "Phase2Result",
    "PlaceAndCrane", "build_solution",
]
