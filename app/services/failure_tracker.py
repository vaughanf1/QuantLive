"""Consecutive failure tracking with threshold-based alerting.

Tracks per-job failure counts in memory. When a job exceeds the alert
threshold (3 consecutive failures), signals that a Telegram system
alert should be sent. Resets counter on success. Alerts only once per
failure streak to avoid notification spam.

Exports:
    FailureTracker  -- class-level tracker (no instantiation needed)
"""

from __future__ import annotations


class FailureTracker:
    """Track consecutive failures per job and trigger alerts at threshold.

    All methods are classmethods operating on class-level state. This is
    appropriate because the application runs as a single process with
    MemoryJobStore -- no cross-process coordination needed.

    Usage:
        count = FailureTracker.record_failure("refresh_candles_M15")
        if FailureTracker.should_alert("refresh_candles_M15"):
            # send alert -- will only fire once per streak
            ...

        FailureTracker.record_success("refresh_candles_M15")
        # resets counter and alert flag
    """

    ALERT_THRESHOLD: int = 3
    _counters: dict[str, int] = {}
    _alerted: dict[str, bool] = {}

    @classmethod
    def record_failure(cls, job_id: str) -> int:
        """Record a failure for the given job and return consecutive count.

        Args:
            job_id: Unique identifier for the job (e.g. "refresh_candles_M15").

        Returns:
            Current consecutive failure count after this failure.
        """
        cls._counters[job_id] = cls._counters.get(job_id, 0) + 1
        return cls._counters[job_id]

    @classmethod
    def record_success(cls, job_id: str) -> None:
        """Record a success for the given job, resetting failure count.

        Args:
            job_id: Unique identifier for the job.
        """
        cls._counters[job_id] = 0
        cls._alerted[job_id] = False

    @classmethod
    def should_alert(cls, job_id: str) -> bool:
        """Check whether an alert should be sent for this job.

        Returns True exactly once when the consecutive failure count
        reaches or exceeds ALERT_THRESHOLD. Subsequent calls return
        False until the counter is reset via record_success().

        Args:
            job_id: Unique identifier for the job.

        Returns:
            True if alert threshold reached and not yet alerted.
        """
        count = cls._counters.get(job_id, 0)
        already_alerted = cls._alerted.get(job_id, False)
        if count >= cls.ALERT_THRESHOLD and not already_alerted:
            cls._alerted[job_id] = True
            return True
        return False

    @classmethod
    def get_count(cls, job_id: str) -> int:
        """Return current consecutive failure count for a job.

        Args:
            job_id: Unique identifier for the job.

        Returns:
            Current consecutive failure count (0 if never failed or reset).
        """
        return cls._counters.get(job_id, 0)

    @classmethod
    def reset_all(cls) -> None:
        """Reset all counters and alert flags. Useful for testing."""
        cls._counters.clear()
        cls._alerted.clear()
