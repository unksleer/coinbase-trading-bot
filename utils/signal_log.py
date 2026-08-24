"""
Per-candle signal decision log.

Every strategy evaluation produces a decision: no crossover, a crossover that
passed the filters, or a crossover that a filter rejected. Only the accepted
ones ever become trades, so the rejected ones are invisible in
completed_trades_*.csv. This module records all three so the filters can be
measured against the opportunities they blocked.

One row per closed candle per pair. The bot loop runs every 60s but the closed
candle only advances once per granularity period, so rows are deduplicated on
candle_time to keep one decision per candle rather than ~60 identical copies.
"""
import csv
import os

from utils.logger import setup_logger

logger = setup_logger(name="coinbase_bot.signal_log", log_file="logs/bot.log")

HEADERS = [
    "timestamp", "pair", "candle_time", "close", "position",
    "raw_signal", "final_signal", "blocked_by",
    "sma_short", "sma_long", "spread_pct", "rsi", "ema",
]


def _round(value, digits):
    """Format a possibly-None numeric for CSV output."""
    if value is None:
        return ""
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return ""


class SignalLogger:
    """Appends one decision row per closed candle."""

    def __init__(self, pair: str, log_dir: str = "logs"):
        self.pair = pair.upper()
        slug = self.pair.lower().replace("/", "-")
        self.path = os.path.join(log_dir, f"signal_log_{slug}.csv")
        self._last_candle_time = self._recover_last_candle_time()

    def _recover_last_candle_time(self):
        """
        Read the most recent candle_time already on disk so a restart does not
        append a duplicate row for the candle that was current when the bot
        stopped.
        """
        try:
            with open(self.path, newline="") as handle:
                last = None
                for row in csv.DictReader(handle):
                    last = row
                return last.get("candle_time") if last else None
        except (FileNotFoundError, OSError, csv.Error):
            return None

    def record(self, candle_time: str, close: float, position: str, decision: dict):
        """
        Record one candle decision. Returns True if a row was written, False if
        this candle was already logged.

        decision is the strategy's last_decision mapping: raw_signal,
        final_signal, blocked_by, and whichever indicator values applied.
        """
        if candle_time and candle_time == self._last_candle_time:
            return False

        decision = decision or {}
        row = {
            "timestamp": _utc_now(),
            "pair": self.pair,
            "candle_time": candle_time,
            "close": _round(close, 8),
            "position": position,
            "raw_signal": decision.get("raw_signal", ""),
            "final_signal": decision.get("final_signal", ""),
            "blocked_by": decision.get("blocked_by", ""),
            "sma_short": _round(decision.get("sma_short"), 8),
            "sma_long": _round(decision.get("sma_long"), 8),
            "spread_pct": _round(decision.get("spread_pct"), 4),
            "rsi": _round(decision.get("rsi"), 2),
            "ema": _round(decision.get("ema"), 8),
        }

        try:
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            file_exists = os.path.exists(self.path) and os.path.getsize(self.path) > 0
            with open(self.path, "a", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=HEADERS)
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row)
        except Exception as e:
            # Signal logging is diagnostic only; never let it stop trading.
            logger.warning(f"Failed to write signal log for {self.pair}: {e}")
            return False

        self._last_candle_time = candle_time
        return True


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
