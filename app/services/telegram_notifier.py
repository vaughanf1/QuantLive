"""Telegram notification service for trade signal alerts and outcome updates.

Sends formatted HTML messages via the Telegram Bot API with retry logic
and rate limiting. Designed as fire-and-forget: notification failures are
logged but never raised to the caller.

Exports:
    TelegramNotifier  -- main service class
"""

from __future__ import annotations

import asyncio

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class TelegramNotifier:
    """Sends formatted trade signals and outcomes via Telegram Bot API.

    Features:
        - HTML-formatted messages (avoids MarkdownV2 escaping issues)
        - Retry with exponential backoff (3 attempts) on HTTP errors
        - Rate limiting: max 1 message per second per chat
        - Fire-and-forget wrappers that never raise exceptions
        - Disabled mode when bot_token or chat_id is empty
    """

    TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._rate_lock = asyncio.Lock()
        self._last_send: float = 0.0

    @property
    def enabled(self) -> bool:
        """Return True if both bot_token and chat_id are configured."""
        return bool(self.bot_token and self.chat_id)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    async def _rate_limit(self) -> None:
        """Enforce max 1 message per second to same chat (TELE-05).

        Acquires an asyncio lock, checks elapsed time since last send,
        and sleeps if less than 1.0 second has passed.
        """
        async with self._rate_lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_send
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)
            self._last_send = asyncio.get_event_loop().time()

    # ------------------------------------------------------------------
    # Message delivery with retry
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
    )
    async def _send_message(self, text: str) -> dict:
        """POST to Telegram sendMessage with retry (TELE-04).

        Retries up to 3 times with exponential backoff on HTTP status
        errors and connection errors. Calls rate limiter before each
        attempt to enforce 1 msg/sec.
        """
        await self._rate_limit()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.TELEGRAM_API.format(token=self.bot_token),
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            response.raise_for_status()
            return response.json()

    # ------------------------------------------------------------------
    # Signal formatting (TELE-01, TELE-03)
    # ------------------------------------------------------------------

    def format_signal(self, signal, strategy_name: str = "Unknown") -> str:
        """Format a Signal ORM object as an HTML Telegram message.

        Includes direction arrow, entry/SL/TP levels, R:R ratio,
        confidence percentage, strategy name, and reasoning.

        Args:
            signal: Signal ORM object with entry_price, stop_loss,
                    take_profit_1, take_profit_2, risk_reward,
                    confidence, direction, reasoning attributes.
            strategy_name: Human-readable strategy name.

        Returns:
            HTML-formatted string safe for Telegram parse_mode="HTML".
        """
        arrow = "\u2B06\uFE0F" if signal.direction == "BUY" else "\u2B07\uFE0F"

        # Calculate pip distances (XAUUSD: 1 pip = $0.10)
        pip = 0.10
        entry = float(signal.entry_price)
        sl_pips = round(abs(entry - float(signal.stop_loss)) / pip, 1)
        tp1_pips = round(abs(float(signal.take_profit_1) - entry) / pip, 1)
        tp2_pips = round(abs(float(signal.take_profit_2) - entry) / pip, 1)

        return (
            f"{arrow} <b>XAUUSD {signal.direction}</b>\n\n"
            f"<b>Entry:</b> {signal.entry_price}\n"
            f"<b>Stop Loss:</b> {signal.stop_loss} ({sl_pips} pips)\n"
            f"<b>TP1:</b> {signal.take_profit_1} ({tp1_pips} pips)\n"
            f"<b>TP2:</b> {signal.take_profit_2} ({tp2_pips} pips)\n"
            f"<b>R:R:</b> {signal.risk_reward}\n"
            f"<b>Confidence:</b> {signal.confidence}%\n"
            f"<b>Strategy:</b> {strategy_name}\n\n"
            f"<i>{signal.reasoning or ''}</i>"
        )

    # ------------------------------------------------------------------
    # Outcome formatting (TELE-02, TELE-03)
    # ------------------------------------------------------------------

    def format_outcome(self, signal, outcome) -> str:
        """Format a Signal + Outcome pair as an HTML Telegram message.

        Includes result emoji, direction, result type, entry/exit prices,
        P&L in pips, and duration in minutes.

        Args:
            signal: Signal ORM object.
            outcome: Outcome ORM object with result, exit_price,
                     pnl_pips, duration_minutes attributes.

        Returns:
            HTML-formatted string safe for Telegram parse_mode="HTML".
        """
        emoji_map = {
            "tp1_hit": "\u2705",
            "tp2_hit": "\u2705\u2705",
            "sl_hit": "\u274C",
            "expired": "\u23F0",
        }
        emoji = emoji_map.get(outcome.result, "")
        return (
            f"{emoji} <b>XAUUSD {signal.direction} - {outcome.result.upper()}</b>\n\n"
            f"<b>Entry:</b> {signal.entry_price}\n"
            f"<b>Exit:</b> {outcome.exit_price}\n"
            f"<b>P&amp;L:</b> {outcome.pnl_pips} pips\n"
            f"<b>Duration:</b> {outcome.duration_minutes} min"
        )

    # ------------------------------------------------------------------
    # Fire-and-forget wrappers
    # ------------------------------------------------------------------

    async def notify_signal(self, signal, strategy_name: str = "Unknown") -> None:
        """Send a signal alert via Telegram. Never raises.

        Checks if notifier is enabled first. Logs success or failure
        without propagating exceptions to the caller.

        Args:
            signal: Signal ORM object (must have .id attribute).
            strategy_name: Human-readable strategy name.
        """
        if not self.enabled:
            logger.debug("Telegram disabled, skipping signal notification")
            return

        try:
            text = self.format_signal(signal, strategy_name=strategy_name)
            await self._send_message(text)
            logger.info(
                "Telegram signal notification sent for signal_id={}",
                signal.id,
            )
        except Exception:
            logger.exception(
                "Telegram signal notification failed for signal_id={}",
                signal.id,
            )

    async def notify_outcome(self, signal, outcome) -> None:
        """Send an outcome alert via Telegram. Never raises.

        Checks if notifier is enabled first. Logs success or failure
        without propagating exceptions to the caller.

        Args:
            signal: Signal ORM object.
            outcome: Outcome ORM object.
        """
        if not self.enabled:
            logger.debug("Telegram disabled, skipping outcome notification")
            return

        try:
            text = self.format_outcome(signal, outcome)
            await self._send_message(text)
            logger.info(
                "Telegram outcome notification sent for signal_id={}",
                signal.id,
            )
        except Exception:
            logger.exception(
                "Telegram outcome notification failed for signal_id={}",
                signal.id,
            )

    # ------------------------------------------------------------------
    # Degradation / recovery formatting (FEED-03, FEED-04)
    # ------------------------------------------------------------------

    def format_degradation(
        self, strategy_name: str, reason: str, is_recovery: bool = False
    ) -> str:
        """Format a degradation or recovery alert as HTML.

        Args:
            strategy_name: Name of the affected strategy.
            reason: Why degradation was detected or recovery occurred.
            is_recovery: True for recovery alerts, False for degradation.
        """
        if is_recovery:
            return (
                f"\U0001f504 <b>Strategy Recovered: {strategy_name}</b>\n\n"
                f"<i>{reason}</i>"
            )
        return (
            f"\u26a0\ufe0f <b>Strategy Degraded: {strategy_name}</b>\n\n"
            f"<b>Reason:</b> {reason}\n\n"
            f"<i>Strategy has been auto-deprioritized. "
            f"Will auto-recover if metrics improve over 7+ days.</i>"
        )

    async def notify_degradation(
        self, strategy_name: str, reason: str, is_recovery: bool = False
    ) -> None:
        """Send a degradation/recovery alert via Telegram. Never raises."""
        if not self.enabled:
            logger.debug("Telegram disabled, skipping degradation notification")
            return
        try:
            text = self.format_degradation(strategy_name, reason, is_recovery)
            await self._send_message(text)
            label = "recovery" if is_recovery else "degradation"
            logger.info(
                "Telegram {} notification sent for '{}'",
                label,
                strategy_name,
            )
        except Exception:
            logger.exception(
                "Telegram degradation notification failed for '{}'",
                strategy_name,
            )

    # ------------------------------------------------------------------
    # Circuit breaker formatting (FEED-05)
    # ------------------------------------------------------------------

    def format_circuit_breaker(self, reason: str, active: bool) -> str:
        """Format circuit breaker activation/deactivation alert."""
        if active:
            return (
                f"\U0001f6d1 <b>CIRCUIT BREAKER ACTIVATED</b>\n\n"
                f"<b>Reason:</b> {reason}\n\n"
                f"<i>Signal generation halted. Auto-resets after 24 hours.</i>"
            )
        return (
            f"\u2705 <b>Circuit Breaker Reset</b>\n\n"
            f"<i>Signal generation resumed. {reason}</i>"
        )

    async def notify_circuit_breaker(self, reason: str, active: bool) -> None:
        """Send circuit breaker alert. Never raises."""
        if not self.enabled:
            return
        try:
            text = self.format_circuit_breaker(reason, active)
            await self._send_message(text)
            logger.info(
                "Telegram circuit breaker notification sent (active={})",
                active,
            )
        except Exception:
            logger.exception("Telegram circuit breaker notification failed")

    # ------------------------------------------------------------------
    # System alert formatting (PROD-02)
    # ------------------------------------------------------------------

    def format_system_alert(self, title: str, details: str) -> str:
        """Format a system alert as an HTML Telegram message.

        Used for operational alerts such as consecutive job failures,
        database connectivity issues, or other infrastructure problems.

        Args:
            title: Short alert title (e.g. "Candle Refresh Failing").
            details: Detailed description of the issue.

        Returns:
            HTML-formatted string safe for Telegram parse_mode="HTML".
        """
        return (
            f"\u26a0\ufe0f <b>SYSTEM ALERT: {title}</b>\n\n"
            f"{details}"
        )

    async def notify_system_alert(self, title: str, details: str) -> None:
        """Send a system alert via Telegram. Never raises.

        Args:
            title: Short alert title.
            details: Detailed description of the issue.
        """
        if not self.enabled:
            logger.debug("Telegram disabled, skipping system alert")
            return
        try:
            text = self.format_system_alert(title, details)
            await self._send_message(text)
            logger.info(
                "Telegram system alert sent: '{}'", title,
            )
        except Exception:
            logger.exception(
                "Telegram system alert failed: '{}'", title,
            )

    # ------------------------------------------------------------------
    # Health digest formatting (PROD-02)
    # ------------------------------------------------------------------

    def format_health_digest(self, stats: dict) -> str:
        """Format a daily health digest as an HTML Telegram message.

        Args:
            stats: Dictionary of operational statistics. Expected keys:
                - active_signals: Number of active signals
                - outcomes_today: Number of outcomes detected today
                - candles_m15/h1/h4/d1: Candle counts by timeframe
                - retention_results: Dict of pruned row counts (optional)
                - job_failures: Dict of job_id -> consecutive failure count
                - uptime_hours: Hours since last restart (optional)

        Returns:
            HTML-formatted string safe for Telegram parse_mode="HTML".
        """
        lines = ["\U0001f4ca <b>Daily Health Digest</b>\n"]

        # Signals & outcomes
        active = stats.get("active_signals", 0)
        outcomes = stats.get("outcomes_today", 0)
        lines.append(f"<b>Active signals:</b> {active}")
        lines.append(f"<b>Outcomes today:</b> {outcomes}")
        lines.append("")

        # Candle counts
        lines.append("<b>Candle Data:</b>")
        for tf in ["M15", "H1", "H4", "D1"]:
            key = f"candles_{tf.lower()}"
            count = stats.get(key, "N/A")
            lines.append(f"  {tf}: {count}")
        lines.append("")

        # Retention results (if available)
        retention = stats.get("retention_results")
        if retention:
            lines.append("<b>Last Retention Run:</b>")
            for key, count in retention.items():
                lines.append(f"  {key}: {count} pruned")
            lines.append("")

        # Job failure counts
        failures = stats.get("job_failures", {})
        if any(v > 0 for v in failures.values()):
            lines.append("<b>Job Failures:</b>")
            for job_id, count in failures.items():
                if count > 0:
                    lines.append(f"  \u26a0\ufe0f {job_id}: {count} consecutive")
            lines.append("")

        return "\n".join(lines)

    async def notify_health_digest(self, stats: dict) -> None:
        """Send a daily health digest via Telegram. Never raises.

        Args:
            stats: Dictionary of operational statistics for digest.
        """
        if not self.enabled:
            logger.debug("Telegram disabled, skipping health digest")
            return
        try:
            text = self.format_health_digest(stats)
            await self._send_message(text)
            logger.info("Telegram health digest sent")
        except Exception:
            logger.exception("Telegram health digest failed")
