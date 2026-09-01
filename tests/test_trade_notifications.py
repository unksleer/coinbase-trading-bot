"""
Alerts must fire on a real fill and on nothing else.

The failure this addresses: a legacy watchlist scanner emailed
"BUY SIGNAL: BTC" every ten minutes whenever a coin merely approached a setup.
It read exactly like a trade confirmation and was taken for one. Meanwhile the
bots that actually traded sent nothing at all — notifications existed only on
the live code path, so a fleet running entirely in simulation was silent, and
the twelve stock bots had no notification code whatsoever.

So the only email that ever arrived was the one that did not mean a trade had
happened.
"""
import os
import re
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)


def _source(name):
    with open(os.path.join(DIR, name)) as handle:
        return handle.read()


def _call_after(src, marker, func="send_trade_notification("):
    """
    The complete call following a marker, parentheses balanced.

    A fixed-width slice is not enough — an explanatory comment between the
    marker and the call pushed the arguments outside the window, so the test
    read half a call and failed on code that was correct.
    """
    start = src.index(func, src.index(marker)) + len(func)
    depth, out = 1, []
    for ch in src[start:]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        out.append(ch)
    return "".join(out)


# ── the paper paths must notify ─────────────────────────────────────────────

def test_crypto_dry_run_buy_notifies():
    call = _call_after(_source("bot.py"), "[DRY RUN] BUY")
    assert re.search(r'"BUY",\s*Settings\.TRADING_PAIR', call)


def test_crypto_dry_run_sell_notifies_with_pnl():
    call = _call_after(_source("bot.py"), "[DRY RUN] Sold")
    assert re.search(r'"SELL",\s*Settings\.TRADING_PAIR', call)
    assert "realized_pnl" in call


def test_crypto_paper_notifications_are_flagged_as_dry_run():
    """A paper fill must never be labelled LIVE MONEY."""
    src = _source("bot.py")
    for marker in ("[DRY RUN] BUY", "[DRY RUN] Sold"):
        args = [a.strip() for a in _call_after(src, marker).split(",")]
        # positional: side, pair, price, amount_usd, amount_base, fee, is_dry_run
        assert args[6] == "True", f"{marker} must pass is_dry_run=True, got {args[6]!r}"


def test_stock_paper_buy_and_sell_notify():
    src = _source("stock_bot.py")
    assert src.count("_notify(") >= 3          # definition plus both sides
    buy = src.split('BUY {shares:.6f}', 1)[1][:400]
    assert '_notify("BUY"' in buy
    sell = src.split('SELL {shares:.6f}', 1)[1][:400]
    assert '_notify("SELL"' in sell


def test_stock_notification_failure_cannot_stop_trading():
    """An SMTP outage is not a reason for a bot to die."""
    src = _source("stock_bot.py")
    body = src.split("def _notify(", 1)[1][:1200]
    assert "try:" in body and "except Exception" in body


# ── the subject must not read like a signal alert ───────────────────────────

def _subject(trade_type, pair, amount_usd, is_dry_run, realized_pnl=0.0):
    mode = "PAPER" if is_dry_run else "LIVE MONEY"
    outcome = (f" · {'+' if realized_pnl >= 0 else '-'}${abs(realized_pnl):,.2f}"
               if trade_type == "SELL" else "")
    return (f"[{mode}] {trade_type} {pair} · ${amount_usd:,.2f}{outcome}"
            f" — position {'opened' if trade_type == 'BUY' else 'closed'}")


def test_subject_states_a_completed_action_not_a_signal():
    subject = _subject("BUY", "BTC-USDC", 10.0, True)
    assert "position opened" in subject
    assert "SIGNAL" not in subject.upper()
    assert "🚀" not in subject


def test_subject_distinguishes_paper_from_live():
    assert _subject("BUY", "BTC-USDC", 10.0, True).startswith("[PAPER]")
    assert _subject("BUY", "BTC-USDC", 10.0, False).startswith("[LIVE MONEY]")


def test_sell_subject_carries_the_outcome():
    assert "+$1.34" in _subject("SELL", "BTC-USDC", 11.34, True, 1.34)
    assert "-$0.22" in _subject("SELL", "SPY", 9.78, True, -0.22)


def test_notifications_module_matches_this_format():
    src = _source(os.path.join("utils", "notifications.py"))
    assert '"PAPER" if is_dry_run else "LIVE MONEY"' in src
    # Check the SUBJECT specifically. The rocket still appears in a comment
    # recording why it was removed, which is documentation, not a subject line.
    subject_line = next(l for l in src.splitlines() if "subject = (" in l)
    tail = src[src.index(subject_line):][:400]
    assert "position " in tail
    assert "🚀" not in tail, "the rocket is what made the scanner alert look like a fill"
    assert "SIGNAL" not in tail.upper()


# ── the legacy scanner must stay gone ───────────────────────────────────────

def test_watchlist_scanner_is_removed():
    """
    daily_report.py mailed a BUY alert every ten minutes for 15 coins — six of
    which had no bot — using one coin's parameters for all of them.
    """
    assert not os.path.exists(os.path.join(DIR, "daily_report.py"))


def test_pnl_report_identifies_bots_by_script_not_env_var():
    """
    Stock bots inherit TRADING_PAIR from .env, so reading it first labelled all
    twelve of them BTC-USDC — xlu-bot reported its P&L as BTC.
    """
    src = _source("daily_pnl_report.py")
    idx = src.find('script == "stock_bot.py"')
    assert idx > 0, "must branch on the script"
    assert src.find('pm_exec_path', idx - 600, idx) > 0
