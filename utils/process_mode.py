"""
Which trading-bot processes are running, and whether each is in paper mode —
read from the process itself, not from pm2's environment snapshot.

pm2 replays the environment a process was FIRST started with, so its view of
DRY_RUN can be stale (that snapshot is what put nine crypto bots on BTC in
August). The process's own environment under /proc is what the bot actually
read at startup. A bot is identified by the script it runs, not by its pm2
name, so a bot named without the "-bot" suffix (fcr-spy) is not missed.

Mode is "Unknown" when the governing variable cannot be read or is absent.
It is never defaulted to "Simulated": a badge that says paper when nothing
was checked is worse than one that says it does not know.
"""
import os

# script basename -> the variable that decides paper vs live in that script
GOVERNING = {
    "bot.py": "DRY_RUN",            # true  => paper (Coinbase dry-run)
    "stock_bot.py": "ALPACA_PAPER", # true  => paper (Alpaca paper endpoint)
    "fcr_bot.py": "ALPACA_PAPER",
}
TRUE = ("true", "1", "t", "yes", "")   # config/settings.py: "" counts as true for DRY_RUN
FALSE = ("false", "0", "f", "no")


def read_environ(pid):
    """The process's environment as a dict, or None if it cannot be read."""
    try:
        with open(f"/proc/{int(pid)}/environ", "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError, TypeError):
        return None
    env = {}
    for item in raw.split(b"\0"):
        if b"=" in item:
            k, _, v = item.partition(b"=")
            env[k.decode(errors="replace")] = v.decode(errors="replace")
    return env


def _crypto_dry_run(env, name):
    """
    Mirror config.settings.get_env_with_profile for DRY_RUN: coin-specific
    key, then profile-specific key, then the base key.
    """
    coin = (env.get("PRODUCT_ID") or env.get("PAIR") or "").split("-")[0].upper()
    if not coin and name.endswith("-bot"):
        coin = name[:-4].upper()
    profile = (env.get(f"{coin}_ACTIVE_PROFILE") if coin else None) or env.get("ACTIVE_PROFILE") or ""
    for key in ([f"{coin}_DRY_RUN"] if coin else []) + ([f"{profile}_DRY_RUN"] if profile else []) + ["DRY_RUN"]:
        if key in env:
            return env[key]
    return None


def resolve_mode(script, env, args=(), name=""):
    """'Simulated' | 'Live' | 'Unknown' for one process."""
    if "--live" in (args or []):
        return "Live"
    if env is None:
        return "Unknown"
    if script == "bot.py":
        raw = _crypto_dry_run(env, name)
        if raw is None:
            return "Unknown"
        val = raw.strip().lower()
        return "Simulated" if val in TRUE else ("Live" if val in FALSE else "Unknown")
    key = GOVERNING.get(script)
    if key is None or key not in env:
        return "Unknown"
    val = env[key].strip().lower()
    if val in ("true", "1", "t", "yes"):
        return "Simulated"
    if val in FALSE:
        return "Live"
    return "Unknown"


def bots_from_pm2(processes, read_env=read_environ):
    """
    The trading-bot rows for the dashboard, from a parsed `pm2 jlist`.
    Only processes running a trading script are bots; the dashboard, the
    observer, collectors and screeners are not, whatever they are named.
    """
    rows = []
    for p in processes:
        env = p.get("pm2_env", {}) or {}
        script = os.path.basename(env.get("pm_exec_path") or "")
        if script not in GOVERNING:
            continue
        status = str(env.get("status", "unknown")).upper()
        proc_env = read_env(p.get("pid")) if status == "ONLINE" else None
        monit = p.get("monit", {}) or {}
        rows.append({
            "name": p.get("name", ""),
            "script": script,
            "status": status,
            "mode": resolve_mode(script, proc_env, env.get("args") or [], p.get("name", "")),
            "memory": f"{(monit.get('memory') or 0) / (1024 * 1024):.1f} MB",
            "cpu": f"{monit.get('cpu', 0)}%",
            "restarts": env.get("restart_time", 0),
            "unstable_restarts": env.get("unstable_restarts", 0),
        })
    return rows
