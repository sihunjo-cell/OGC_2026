"""Phase0.config -- 전처리 튜닝 파라미터와 공용 상수."""

# Douglas-Peucker 허용오차. 0.0=원본 유지(안전 기본값). 평가 서버가 원본
# 폴리곤으로 검증하므로 안쪽으로 단순화하면 불가능한 배치가 통과될 수 있음.
DP_TOL: float = 0.0

# 지오메트리 필터 수치 허용오차.
EPS: float = 1e-9

# NFP 생성 백엔드 (OGC_GEOM 환경변수):
#   "fast"    -- 기본값: 순수 파이썬 Minkowski + shapely union ("shapely"와 동일 ring).
#   "shapely" -- 레퍼런스 (shapely MultiPoint Minkowski + unary_union).
#   "pieces"  -- union 없는 볼록 조각 (실험용, 목적함수 바뀜).
import os as _os
GEOM_MODE: str = _os.environ.get("OGC_GEOM", "fast").lower()
