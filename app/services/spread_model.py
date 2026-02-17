"""Session-aware spread model for XAUUSD backtesting.

Returns realistic spread costs based on the active forex trading session
at a given timestamp. Spreads are tighter during high-liquidity sessions
(London/NY overlap) and wider during low-liquidity periods (Asian session).
"""

from datetime import datetime
from decimal import Decimal

from app.strategies.helpers.session_filter import get_active_sessions


class SessionSpreadModel:
    """Provides session-aware spread estimates for XAUUSD.

    Spread values are in price units (not pips). For XAUUSD where
    1 pip = $0.10, a spread of 0.30 represents 3 pips.

    When multiple sessions overlap, the tightest (minimum) spread
    is used since liquidity is highest during overlaps.
    """

    SESSION_SPREADS: dict[str, Decimal] = {
        "overlap": Decimal("0.20"),   # London/NY overlap: ~2 pips (tightest)
        "london": Decimal("0.30"),    # London session: ~3 pips
        "new_york": Decimal("0.30"),  # NY session: ~3 pips
        "asian": Decimal("0.50"),     # Asian session: ~5 pips (widest)
    }

    DEFAULT_SPREAD: Decimal = Decimal("0.50")  # Off-session / unknown: conservative

    def get_spread(self, timestamp: datetime) -> Decimal:
        """Return the session-appropriate spread for a given timestamp.

        When multiple sessions are active, returns the tightest (minimum)
        spread since liquidity is highest during session overlaps.

        Args:
            timestamp: UTC datetime to look up the active session for.

        Returns:
            Spread in price units as a Decimal.
        """
        active_sessions = get_active_sessions(timestamp)

        if not active_sessions:
            return self.DEFAULT_SPREAD

        # Return the tightest spread among active sessions
        spreads = [
            self.SESSION_SPREADS[session]
            for session in active_sessions
            if session in self.SESSION_SPREADS
        ]

        if not spreads:
            return self.DEFAULT_SPREAD

        return min(spreads)
