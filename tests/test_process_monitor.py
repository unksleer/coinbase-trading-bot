"""
The System Process Monitor must list bots by the script they run, read mode
from the process itself, and never default an unread mode to paper.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.process_mode import bots_from_pm2, resolve_mode


def proc(name, script, pid=100, status="online", args=None):
    return {"name": name, "pid": pid, "monit": {"memory": 1048576, "cpu": 0},
            "pm2_env": {"pm_exec_path": f"/x/{script}", "status": status, "args": args or [],
                        "restart_time": 3, "unstable_restarts": 0, "DRY_RUN": "False"}}  # stale snapshot says LIVE


def test_picks_bots_by_script_not_name():
    procs = [proc("dashboard", "dashboard.py"), proc("fcr-spy", "fcr_bot.py"),
             proc("observer", "observer.py"), proc("sol-bot", "bot.py"), proc("spy-bot", "stock_bot.py")]
    names = [r["name"] for r in bots_from_pm2(procs, read_env=lambda pid: {"ALPACA_PAPER": "True", "DRY_RUN": "True"})]
    assert names == ["fcr-spy", "sol-bot", "spy-bot"]


def test_mode_comes_from_process_not_pm2_snapshot():
    rows = bots_from_pm2([proc("spy-bot", "stock_bot.py")], read_env=lambda pid: {"ALPACA_PAPER": "True"})
    assert rows[0]["mode"] == "Simulated"   # snapshot said DRY_RUN=False; the process says paper


def test_stock_bot_is_judged_on_alpaca_paper_not_dry_run():
    assert resolve_mode("stock_bot.py", {"DRY_RUN": "True"}) == "Unknown"
    assert resolve_mode("stock_bot.py", {"ALPACA_PAPER": "False"}) == "Live"
    assert resolve_mode("fcr_bot.py", {"ALPACA_PAPER": "True"}) == "Simulated"


def test_unreadable_or_absent_is_unknown_never_simulated():
    assert resolve_mode("bot.py", None) == "Unknown"
    assert resolve_mode("bot.py", {}) == "Unknown"
    assert resolve_mode("stock_bot.py", {}) == "Unknown"
    rows = bots_from_pm2([proc("sol-bot", "bot.py")], read_env=lambda pid: None)
    assert rows[0]["mode"] == "Unknown"


def test_crypto_coin_and_profile_overrides_win_like_settings_do():
    env = {"DRY_RUN": "True", "SOL_ACTIVE_PROFILE": "BREAKOUT", "BREAKOUT_DRY_RUN": "False"}
    assert resolve_mode("bot.py", env, name="sol-bot") == "Live"
    env = {"DRY_RUN": "False", "SOL_DRY_RUN": "True"}
    assert resolve_mode("bot.py", env, name="sol-bot") == "Simulated"
    assert resolve_mode("bot.py", {"DRY_RUN": "True"}, args=["--live"]) == "Live"


def test_stopped_process_is_listed_without_reading_proc():
    rows = bots_from_pm2([proc("xlu-bot", "stock_bot.py", status="stopped")],
                         read_env=lambda pid: (_ for _ in ()).throw(AssertionError("must not read")))
    assert rows[0]["status"] == "STOPPED" and rows[0]["mode"] == "Unknown"
