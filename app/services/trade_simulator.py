"""Trade simulator: walks CandidateSignals through OHLC bars to determine outcomes.

Simulates trades forward from signal entry through subsequent candles,
checking for stop-loss, take-profit, or expiry conditions. SL always
takes priority over TP when both could be hit in the same bar
(conservative assumption).
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

import pandas as pd

from app.strategies.base import CandidateSignal, Direction


class TradeOutcome(str, Enum):
    """Possible outcomes for a simulated trade."""

    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    SL_HIT = "SL_HIT"
    EXPIRED = "EXPIRED"


@dataclass
class SimulatedTrade:
    """Result of simulating a single CandidateSignal through OHLC data."""

    signal: CandidateSignal
    outcome: TradeOutcome
    exit_price: Decimal
    pnl_pips: Decimal
    bars_held: int
    spread_cost: Decimal


class TradeSimulator:
    """Simulates trades by walking CandidateSignals forward through OHLC bars.

    For each signal, the simulator enters the trade on the bar after the
    signal bar and checks subsequent bars for SL/TP hits or expiry.

    Attributes:
        MAX_BARS_FORWARD: Maximum bars to hold a trade before expiry (72 = 3 days at H1).
        PIP_VALUE: Price movement per pip for XAUUSD ($0.10).
    """

    MAX_BARS_FORWARD = 72
    PIP_VALUE = 0.10

    def simulate_trade(
        self,
        signal: CandidateSignal,
        candles: pd.DataFrame,
        signal_bar_idx: int,
        spread: Decimal,
    ) -> SimulatedTrade:
        """Simulate a single trade through OHLC bars.

        Args:
            signal: The candidate signal to simulate.
            candles: DataFrame with columns [timestamp, open, high, low, close].
            signal_bar_idx: Index of the bar that generated the signal.
            spread: Spread cost in price units (e.g. Decimal("0.30")).

        Returns:
            SimulatedTrade with outcome, exit price, PnL in pips, and bars held.
        """
        # Convert signal prices to float for internal math
        entry = float(signal.entry_price)
        sl = float(signal.stop_loss)
        tp1 = float(signal.take_profit_1)
        tp2 = float(signal.take_profit_2)
        spread_f = float(spread)
        is_buy = signal.direction == Direction.BUY

        # Adjust entry for spread
        if is_buy:
            # Buying at ask = bid + spread
            adjusted_entry = entry + spread_f
        else:
            # Selling at bid (no adjustment needed for entry)
            adjusted_entry = entry

        # Walk forward through bars after the signal bar
        start_idx = signal_bar_idx + 1
        end_idx = min(signal_bar_idx + 1 + self.MAX_BARS_FORWARD, len(candles))

        for i in range(start_idx, end_idx):
            bar = candles.iloc[i]
            bar_high = float(bar["high"])
            bar_low = float(bar["low"])
            bars_held = i - signal_bar_idx

            # Check SL first (conservative: SL takes priority over TP in same bar)
            if is_buy:
                sl_hit = bar_low <= sl
            else:
                # For SELL, SL is above entry; check ask side (high + spread)
                sl_hit = (bar_high + spread_f) >= sl

            if sl_hit:
                exit_price_f = sl
                pnl = (exit_price_f - adjusted_entry) if is_buy else (adjusted_entry - exit_price_f)
                pnl_pips = pnl / self.PIP_VALUE
                return SimulatedTrade(
                    signal=signal,
                    outcome=TradeOutcome.SL_HIT,
                    exit_price=Decimal(str(round(exit_price_f, 2))),
                    pnl_pips=Decimal(str(round(pnl_pips, 2))),
                    bars_held=bars_held,
                    spread_cost=spread,
                )

            # Check TP2 next (higher priority than TP1)
            if is_buy:
                tp2_hit = bar_high >= tp2
            else:
                tp2_hit = bar_low <= tp2

            if tp2_hit:
                exit_price_f = tp2
                pnl = (exit_price_f - adjusted_entry) if is_buy else (adjusted_entry - exit_price_f)
                pnl_pips = pnl / self.PIP_VALUE
                return SimulatedTrade(
                    signal=signal,
                    outcome=TradeOutcome.TP2_HIT,
                    exit_price=Decimal(str(round(exit_price_f, 2))),
                    pnl_pips=Decimal(str(round(pnl_pips, 2))),
                    bars_held=bars_held,
                    spread_cost=spread,
                )

            # Check TP1 last
            if is_buy:
                tp1_hit = bar_high >= tp1
            else:
                tp1_hit = bar_low <= tp1

            if tp1_hit:
                exit_price_f = tp1
                pnl = (exit_price_f - adjusted_entry) if is_buy else (adjusted_entry - exit_price_f)
                pnl_pips = pnl / self.PIP_VALUE
                return SimulatedTrade(
                    signal=signal,
                    outcome=TradeOutcome.TP1_HIT,
                    exit_price=Decimal(str(round(exit_price_f, 2))),
                    pnl_pips=Decimal(str(round(pnl_pips, 2))),
                    bars_held=bars_held,
                    spread_cost=spread,
                )

        # Expired: no SL or TP hit within MAX_BARS_FORWARD
        last_bar_idx = end_idx - 1
        if last_bar_idx < start_idx:
            # Edge case: no bars available after signal
            exit_price_f = adjusted_entry
            bars_held = 0
        else:
            exit_price_f = float(candles.iloc[last_bar_idx]["close"])
            bars_held = last_bar_idx - signal_bar_idx

        pnl = (exit_price_f - adjusted_entry) if is_buy else (adjusted_entry - exit_price_f)
        pnl_pips = pnl / self.PIP_VALUE

        return SimulatedTrade(
            signal=signal,
            outcome=TradeOutcome.EXPIRED,
            exit_price=Decimal(str(round(exit_price_f, 2))),
            pnl_pips=Decimal(str(round(pnl_pips, 2))),
            bars_held=bars_held,
            spread_cost=spread,
        )

    def simulate_signals(
        self,
        signals: list[tuple[CandidateSignal, int]],
        candles: pd.DataFrame,
        spread_model: object,
    ) -> list[SimulatedTrade]:
        """Simulate multiple signals through OHLC bars.

        Convenience method that processes a list of (signal, signal_bar_idx)
        tuples, obtaining spreads from the spread model for each signal.

        Args:
            signals: List of (CandidateSignal, signal_bar_idx) tuples.
            candles: DataFrame with OHLC data.
            spread_model: Object with get_spread(timestamp) -> Decimal method.

        Returns:
            List of SimulatedTrade results.
        """
        trades: list[SimulatedTrade] = []
        for signal, signal_bar_idx in signals:
            spread = spread_model.get_spread(signal.timestamp)
            trade = self.simulate_trade(signal, candles, signal_bar_idx, spread)
            trades.append(trade)
        return trades
