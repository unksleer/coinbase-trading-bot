"""
The signal table's position must come from the state file, not a log.

The signal log is written once per closed candle; on six-hour bars that is
four times a day. Trades happen between closes. So its `position` column can
lag reality by up to six hours — ADA showed IN TRADE three hours after selling,
BTC showed ENTERING two hours after buying, on the same page as an
active-positions panel that read the state files and had both right.
"""
import csv
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

import dashboard  # noqa: E402

SRC = open(os.path.join(DIR, "dashboard.py")).read()


def test_held_set_is_built_from_state_files_once_per_request():
    assert 'held = {p["symbol"].upper() for p in _positions.open_positions()}' in SRC
    assert "self.signal_from_log(pair, env, held=held)" in SRC


def test_scrape_path_uses_the_same_truth():
    assert '"position": ("LONG" if display_pair.upper() in held else "NONE")' in SRC


def _handler_with_log(tmp_path, monkeypatch, position):
    logs = tmp_path / "logs"; logs.mkdir()
    with open(logs / "signal_log_ada-usdc.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["timestamp", "pair", "candle_time", "close",
                                           "position", "raw_signal", "final_signal",
                                           "blocked_by", "sma_short", "sma_long",
                                           "spread_pct", "rsi", "ema", "strategy"])
        w.writeheader()
        w.writerow({"timestamp": "t", "pair": "ADA-USDC", "candle_time": "c",
                    "close": "0.2", "position": position, "raw_signal": "HOLD",
                    "final_signal": "HOLD", "blocked_by": "", "sma_short": "0.21",
                    "sma_long": "0.20", "spread_pct": "1", "rsi": "50", "ema": "0.2",
                    "strategy": "SMA_CROSSOVER"})
    # signal_from_log locates logs relative to dashboard.py; point it at tmp.
    monkeypatch.setattr(dashboard.os.path, "dirname", lambda p: str(tmp_path))
    h = dashboard.DashboardHandler.__new__(dashboard.DashboardHandler)
    return h


def test_a_stale_long_in_the_log_reads_flat_when_the_state_file_is_flat(tmp_path, monkeypatch):
    """ADA: sold at 14:48, log row from 12:00 still said LONG."""
    h = _handler_with_log(tmp_path, monkeypatch, position="LONG")
    entry = h.signal_from_log("ADA-USDC", {}, held=set())
    assert entry["position"] == "NONE"
    assert entry["signal"] != "IN TRADE"


def test_a_stale_none_in_the_log_reads_in_trade_when_the_state_file_is_long(tmp_path, monkeypatch):
    """BTC: bought at 14:12, log row from 12:00 still said NONE."""
    h = _handler_with_log(tmp_path, monkeypatch, position="NONE")
    entry = h.signal_from_log("ADA-USDC", {}, held={"ADA-USDC"})
    assert entry["position"] == "LONG"
    assert entry["signal"] == "IN TRADE"


def test_stock_prefix_is_stripped_for_the_lookup(tmp_path, monkeypatch):
    """BOT_MAP names stock pairs 'stock-WMT'; state files know them as 'WMT'."""
    assert 'pair.replace("stock-", "").upper() in held' in SRC


def test_without_a_held_set_the_log_value_is_still_honoured(tmp_path, monkeypatch):
    """Backward compatible for any caller that has no state-file view."""
    h = _handler_with_log(tmp_path, monkeypatch, position="LONG")
    assert h.signal_from_log("ADA-USDC", {})["position"] == "LONG"
