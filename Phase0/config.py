"""Phase0.config -- 전처리 튜닝 파라미터와 공용 상수."""

# 지오메트리 필터 수치 허용오차.
EPS: float = 1e-9

# NFP 백엔드(OGC_GEOM): fast(기본) / shapely(비트동일 검증 레퍼런스)
import os as _os
GEOM_MODE: str = _os.environ.get("OGC_GEOM", "fast").lower()
