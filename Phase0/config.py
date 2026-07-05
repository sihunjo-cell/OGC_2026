"""Phase0.config -- 전처리 튜닝 파라미터와 공용 상수."""

# 폴리곤 단순화용 Douglas-Peucker 허용오차. 0.0이면 원본 그대로(안전 기본값):
# 평가 서버는 원본 폴리곤으로 검증하므로, 안쪽으로 단순화하면 실제로는
# 불가능한 배치가 가능한 것처럼 잘못 통과될 수 있음.
DP_TOL: float = 0.0

# 지오메트리 필터에서 쓰는 수치 허용오차.
EPS: float = 1e-9

# NFP 생성 백엔드 (OGC_GEOM 환경변수로 변경):
#   "fast"    -- 기본값: 순수 파이썬 Minkowski + shapely union. ring은
#                "shapely"와 동일하지만 5~10배 빠름.
#   "shapely" -- 레퍼런스 경로 (shapely MultiPoint Minkowski + unary_union).
#   "pieces"  -- union 없는 볼록 조각 (가장 빠름. 목적함수 바뀌므로 실험용).
import os as _os
GEOM_MODE: str = _os.environ.get("OGC_GEOM", "fast").lower()
