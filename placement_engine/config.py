ENGINE_VERSION = "0.1.13"

# Geometry tolerances (millimetres). Shapely operations on float coordinates
# can produce sliver artefacts; a small absolute tolerance lets us treat
# near-zero values as zero without masking real bugs.
AREA_EPSILON_MM2 = 1.0
LENGTH_EPSILON_MM = 0.1

# Default overlap tolerance between placed pieces. Two pieces sharing an edge
# may produce a hairline intersection due to floating-point rounding; anything
# below this is treated as touching, not overlapping.
DEFAULT_OVERLAP_TOLERANCE_MM2 = 1.0
