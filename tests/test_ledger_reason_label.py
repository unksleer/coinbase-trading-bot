"""
The ledger's entry and exit reasons must name the strategy that produced
them. They were hardcoded to "SMA Bullish/Bearish Cross", so every Donchian
trade since the 2026-09-21 switch was labelled as an SMA cross.
"""
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from utils.state import StateManager


def test_labels_follow_the_strategy_name():
    sm = StateManager.__new__(StateManager)
    sm.strategy_name = "Donchian_Breakout_80_40"
    assert sm.entry_label() == "Donchian_Breakout_80_40 entry"
    assert sm.exit_label() == "Donchian_Breakout_80_40 exit"


def test_labels_fall_back_only_when_no_strategy_was_set():
    sm = StateManager.__new__(StateManager)
    sm.strategy_name = None
    assert sm.entry_label() == "SMA Bullish Cross"
    assert sm.exit_label() == "SMA Bearish Cross"


def test_bot_has_no_hardcoded_reason_strings_and_sets_the_name():
    src = open(os.path.join(DIR, "bot.py")).read()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert 'reason="SMA Bullish Cross"' not in code
    assert re.search(r"state_manager\.strategy_name\s*=\s*strategy\.name", code)
    # and it is set after the state manager exists and the strategy is built
    assert code.index("state_manager = StateManager(") < code.index("state_manager.strategy_name = strategy.name")
