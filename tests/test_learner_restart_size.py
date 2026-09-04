"""
A learner restart must not change a bot's trade size.

COINS_FALLBACK hardcodes amount=10 for every crypto coin. Granularity in the
same dict is already resolved from .env before use; amount was passed through
raw. After paper size moved to $100 on 2026-09-04, an APPLY_APPROVED learner
run would have restarted each bot back at $10 — a size change hidden inside a
parameter update, with nothing in the log to say so.
"""
import json
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

import auto_learner  # noqa: E402


def _fake_pm2(monkeypatch, payload):
    class R:
        stdout = json.dumps(payload)
    monkeypatch.setattr(auto_learner.subprocess, "run", lambda *a, **k: R())


def test_live_size_wins_over_the_hardcoded_fallback(monkeypatch):
    _fake_pm2(monkeypatch, [{"name": "ada-bot", "pm2_env": {"TRADE_AMOUNT_USD": "100"}}])
    assert auto_learner._live_amount("ada-bot", 10) == 100.0


def test_fallback_used_only_when_the_bot_is_not_found(monkeypatch):
    _fake_pm2(monkeypatch, [{"name": "btc-bot", "pm2_env": {"TRADE_AMOUNT_USD": "100"}}])
    assert auto_learner._live_amount("ada-bot", 10) == 10


def test_fallback_used_when_pm2_is_unreachable(monkeypatch):
    def boom(*a, **k):
        raise OSError("pm2 not running")
    monkeypatch.setattr(auto_learner.subprocess, "run", boom)
    assert auto_learner._live_amount("ada-bot", 10) == 10


def test_restart_list_is_built_from_live_sizes():
    """The call site must go through _live_amount, not cfg['amount'] directly."""
    src = open(os.path.join(DIR, "auto_learner.py")).read()
    assert '_live_amount(cfg["bot"], cfg["amount"])' in src
    assert 'bots_to_restart.append((cfg["bot"], pair, cfg["amount"]))' not in src


def test_risk_caps_match_the_paper_size():
    """
    The $15 caps govern crypto only, and were sized for a $10 trade. At $100
    every order was rejected before reaching the wallet.
    """
    with open(os.path.join(DIR, ".env")) as handle:
        env = dict(l.strip().split("=", 1) for l in handle
                   if "=" in l and not l.startswith("#"))
    assert float(env["MAX_VAL_PER_ORDER"]) >= 100.0
    assert float(env["MAX_EXPOSURE_PER_PRODUCT"]) >= 100.0


def test_no_per_coin_override_of_an_identity_key():
    """
    settings checks {COIN}_{KEY} before the plain key, and the per-coin form is
    not protected by env_loader, so .env silently outranks the pm2 command line
    for that one coin. BTC_TRADE_AMOUNT_USD=10 made btc-bot run at $10 after
    being started at $100. Identity belongs on the command line, never
    per-coin in .env.
    """
    import re
    from utils.env_loader import PROCESS_IDENTITY
    with open(os.path.join(DIR, ".env")) as handle:
        lines = [l.strip() for l in handle if l.strip() and not l.startswith("#")]
    pattern = re.compile(r"^([A-Z0-9]+)_(" + "|".join(PROCESS_IDENTITY) + r")=")
    offenders = [l for l in lines if pattern.match(l)]
    assert not offenders, f"per-coin identity overrides in .env: {offenders}"


def test_a_stale_per_coin_identity_variant_is_stripped_on_load(monkeypatch, tmp_path):
    """
    pm2 replays the environment a process was first started with. A key
    deleted from .env therefore survives in every bot that inherited it, and
    a per-coin variant of an identity key outranks the command-line value.
    btc-bot ran at $10 on a BTC_TRADE_AMOUNT_USD=10 that .env no longer had.
    """
    from utils import env_loader
    env = tmp_path / ".env"
    env.write_text("SOME_TUNING=1\n")                 # .env does NOT set the variant
    monkeypatch.setenv("TRADE_AMOUNT_USD", "100")     # command line: identity
    monkeypatch.setenv("BTC_TRADE_AMOUNT_USD", "10")  # stale pm2 snapshot
    monkeypatch.setenv("SIM_DRY_RUN", "False")        # a profile variant, same class
    env_loader.load(str(env))
    assert os.environ["TRADE_AMOUNT_USD"] == "100"
    assert "BTC_TRADE_AMOUNT_USD" not in os.environ
    assert "SIM_DRY_RUN" not in os.environ
    assert os.environ["SOME_TUNING"] == "1"           # tuning untouched


def test_stripping_does_not_touch_non_identity_per_coin_keys(monkeypatch, tmp_path):
    """BTC_GRANULARITY is tuning, not identity, and must survive."""
    from utils import env_loader
    env = tmp_path / ".env"
    env.write_text("BTC_GRANULARITY=SIX_HOUR\n")
    monkeypatch.setenv("TRADING_PAIR", "BTC-USDC")
    env_loader.load(str(env))
    assert os.environ["BTC_GRANULARITY"] == "SIX_HOUR"
