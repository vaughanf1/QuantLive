"""Forex trading session time windows and filtering functions.

All session times are defined in UTC hours.
"""

from datetime import datetime

# Session definitions: (start_hour_utc, end_hour_utc)
# If start > end, the session wraps past midnight.
SESSIONS: dict[str, tuple[int, int]] = {
    "asian": (23, 8),       # Wraps midnight: 23:00 - 08:00 UTC
    "london": (7, 16),      # 07:00 - 16:00 UTC
    "new_york": (12, 21),   # 12:00 - 21:00 UTC
    "overlap": (12, 16),    # London/NY overlap: 12:00 - 16:00 UTC
}


def _is_hour_in_range(hour: int, start: int, end: int) -> bool:
    """Check if an hour falls within a session range, handling midnight wrap."""
    if start <= end:
        # Normal range (e.g., london: 7-16)
        return start <= hour < end
    else:
        # Wraps midnight (e.g., asian: 23-8)
        return hour >= start or hour < end


def get_active_sessions(timestamp: datetime) -> list[str]:
    """Return list of active session names for a given UTC timestamp.

    Args:
        timestamp: A datetime object (should be UTC).

    Returns:
        List of session name strings that are active at the given time.
        May be empty if no sessions are active.
    """
    hour = timestamp.hour
    active = []
    for session_name, (start, end) in SESSIONS.items():
        if _is_hour_in_range(hour, start, end):
            active.append(session_name)
    return active


def is_in_session(timestamp: datetime, session: str) -> bool:
    """Check if a timestamp falls within a named trading session.

    Args:
        timestamp: A datetime object (should be UTC).
        session: Session name (e.g., "london", "new_york", "asian", "overlap").

    Returns:
        True if the timestamp is within the session window.

    Raises:
        ValueError: If the session name is not recognized.
    """
    if session not in SESSIONS:
        raise ValueError(
            f"Unknown session '{session}'. Available: {list(SESSIONS.keys())}"
        )
    start, end = SESSIONS[session]
    return _is_hour_in_range(timestamp.hour, start, end)
