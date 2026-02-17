"""Strategy helper modules: indicators, swing detection, session filter, market structure.

Re-exports key functions for convenient access:
    from app.strategies.helpers import compute_ema, detect_swing_highs, ...
"""

from app.strategies.helpers.indicators import (
    compute_atr,
    compute_ema,
    compute_rsi,
    compute_vwap,
)
from app.strategies.helpers.market_structure import (
    detect_bos,
    detect_choch,
    detect_structure_shift,
)
from app.strategies.helpers.session_filter import (
    SESSIONS,
    get_active_sessions,
    is_in_session,
)
from app.strategies.helpers.swing_detection import (
    detect_swing_highs,
    detect_swing_lows,
)

__all__ = [
    # Indicators
    "compute_ema",
    "compute_atr",
    "compute_vwap",
    "compute_rsi",
    # Swing detection
    "detect_swing_highs",
    "detect_swing_lows",
    # Session filter
    "get_active_sessions",
    "is_in_session",
    "SESSIONS",
    # Market structure
    "detect_structure_shift",
    "detect_bos",
    "detect_choch",
]
