#!/usr/bin/env python3
import os
import re
import json
import time
import http.server
import socketserver
import csv
import logging
import subprocess
import glob

import requests


PORT = 8080

# Live prices are cached briefly because the browser polls /api/metrics every
# 10s and every open position would otherwise issue its own request.
PRICE_CACHE_TTL_SECONDS = 30
PRICE_REQUEST_TIMEOUT = 4

# Beyond this, a price is old enough that P&L computed from it is misleading
# rather than merely lagging, and the UI says so.
PRICE_STALE_AFTER_SECONDS = 900

logger = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO)


class PriceResolver:
    """
    Resolve a current price for a pair, and say where it came from.

    Prices previously came from logs/market_metrics.json, which is written by
    the scanner on its own schedule. When the scanner stops running that file
    silently goes stale and every unrealized P&L on the dashboard is computed
    against an old price — reporting losses that never happened. Any price this
    returns is therefore tagged with its source and age so the UI can show,
    rather than hide, how much to trust it.

    Order: live ticker -> the bot's own last closed candle -> entry price.
    """

    def __init__(self, project_root):
        self.project_root = project_root
        self._cache = {}          # pair -> (price, fetched_at)
        self._unavailable = {}    # pair -> until_timestamp

    def get(self, pair, entry_price):
        """Return (price, source, age_seconds). Never raises."""
        live = self._live_price(pair)
        if live is not None:
            return live, "live", 0

        candle = self._last_candle_price(pair)
        if candle is not None:
            price, age = candle
            return price, "candle", age

        return entry_price, "entry", None

    def _live_price(self, pair):
        now = time.time()
        cached = self._cache.get(pair)
        if cached and now - cached[1] < PRICE_CACHE_TTL_SECONDS:
            return cached[0]

        # Back off on a pair that just failed rather than retrying every poll.
        blocked_until = self._unavailable.get(pair, 0)
        if now < blocked_until:
            return None

        for product in self._candidate_products(pair):
            try:
                response = requests.get(
                    f"https://api.exchange.coinbase.com/products/{product}/ticker",
                    timeout=PRICE_REQUEST_TIMEOUT,
                )
                if response.status_code != 200:
                    continue
                price = float(response.json()["price"])
            except (requests.RequestException, ValueError, KeyError, TypeError):
                continue
            self._cache[pair] = (price, now)
            self._unavailable.pop(pair, None)
            return price

        self._unavailable[pair] = now + PRICE_CACHE_TTL_SECONDS
        return None

    @staticmethod
    def _candidate_products(pair):
        """
        -USDC pairs are not quoted on this endpoint; their -USD equivalent is.
        Try the requested pair first so a genuine -USDC listing still wins.
        """
        pair = pair.upper()
        products = [pair]
        if pair.endswith("-USDC"):
            products.append(pair[:-5] + "-USD")
        return products

    def _last_candle_price(self, pair):
        """
        Fall back to the close the bot itself last acted on. Returns
        (price, age_seconds) or None.
        """
        slug = pair.lower().replace("/", "-")
        path = os.path.join(self.project_root, "logs", f"signal_log_{slug}.csv")
        try:
            with open(path, newline="") as handle:
                last = None
                for row in csv.DictReader(handle):
                    last = row
            if not last or not last.get("close"):
                return None
            price = float(last["close"])
        except (OSError, csv.Error, ValueError, TypeError):
            return None

        age = None
        try:
            stamp = last.get("timestamp", "").replace("Z", "+00:00")
            from datetime import datetime, timezone
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(stamp)).total_seconds()
        except (ValueError, TypeError):
            age = None
        return price, age

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    # Shared across requests so the price cache actually caches: the handler
    # is constructed fresh for every connection.
    price_resolver = None

    def __init__(self, *args, **kwargs):
        # Serve static files from the 'dashboard' folder
        project_root = os.path.dirname(os.path.abspath(__file__))
        if DashboardHandler.price_resolver is None:
            DashboardHandler.price_resolver = PriceResolver(project_root)
        self.directory = os.path.join(project_root, 'dashboard')
        os.makedirs(self.directory, exist_ok=True)
        super().__init__(*args, directory=self.directory, **kwargs)

    def do_GET(self):
        if self.path == '/api/metrics':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            data = self.get_dashboard_data()
            self.wfile.write(json.dumps(data).encode('utf-8'))
            return

        if self.path == '/api/bot_signals':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(self.get_bot_signals()).encode('utf-8'))
            return

        return super().do_GET()

    def end_headers(self):
        """
        Tell the browser to revalidate instead of serving the page from cache.

        SimpleHTTPRequestHandler sends Last-Modified but no Cache-Control, so a
        browser is free to reuse index.html without asking whether it changed.
        Every dashboard edit then stays invisible until someone thinks to hard
        refresh — which reads as "the change did not work" rather than "the
        page is stale", and sends you looking for a bug that isn't there.

        no-cache still permits caching; it requires revalidation first, so the
        304 path keeps working and only genuinely-changed files are re-sent.
        """
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        super().end_headers()

    @staticmethod
    def _env_values():
        """Parse .env once per request into a flat mapping."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        values = {}
        try:
            with open(path) as handle:
                for line in handle:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        key, _, value = line.partition("=")
                        values[key.strip()] = value.strip()
        except OSError:
            pass
        return values

    @staticmethod
    def _resolve_setting(env, prefix, key, default=None):
        """Coin key, then the coin's active profile, then the bare key."""
        if f"{prefix}_{key}" in env:
            return env[f"{prefix}_{key}"]
        profile = env.get(f"{prefix}_ACTIVE_PROFILE") or env.get("ACTIVE_PROFILE", "")
        profile_key = f"{profile.strip().upper()}_{key}" if profile else ""
        if profile_key and profile_key in env:
            return env[profile_key]
        return env.get(key, default)

    def signal_from_log(self, pair, env):
        """
        Read the bot's own last decision from logs/signal_log_<pair>.csv.

        Preferred over scraping PM2 output: the CSV is written for this purpose,
        so it survives log-format changes, and it carries blocked_by — the
        reason a crossover was rejected, which the log line never stated and no
        regex could recover.

        Returns an entry dict, or None if this bot writes no signal log.
        """
        slug = pair.lower().replace("/", "-")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "logs", f"signal_log_{slug}.csv")
        try:
            with open(path, newline="") as handle:
                last = None
                for row in csv.DictReader(handle):
                    last = row
        except (OSError, csv.Error):
            return None
        if not last:
            return None

        def num(key):
            try:
                return float(last[key])
            except (KeyError, TypeError, ValueError):
                return None

        prefix = pair.split("-")[0].upper()
        rsi_min = self._resolve_setting(env, prefix, "RSI_BUY_MIN")
        rsi_max = self._resolve_setting(env, prefix, "RSI_BUY_MAX")

        short, long = num("sma_short"), num("sma_long")
        direction = None
        if short is not None and long is not None:
            direction = "ABOVE" if short > long else "BELOW"

        position = (last.get("position") or "NONE").upper()
        blocked_by = last.get("blocked_by") or ""
        raw_signal = last.get("raw_signal") or ""

        # blocked_by states the reason directly, so readiness is read off the
        # bot's own decision rather than re-derived from indicator values.
        #
        # Labels describe what the bot is doing, not how likely a trade is. The
        # crossover strategy enters on the crossing itself — a single candle —
        # so a pair already above its long SMA has missed the entry rather than
        # approached one, which is what the old "CLOSE" label implied.
        #
        # SMA geometry is meaningless for a mean-reversion bot, which enters on
        # RSI crossing up through oversold. Deriving it anyway is why the stock
        # bots displayed as ENTERING while sitting at RSI 50.
        strategy = (last.get("strategy") or "").upper()

        if position == "LONG":
            signal = "IN TRADE"
        elif blocked_by:
            signal = "BLOCKED"
        elif raw_signal == "BUY":
            signal = "ENTERING"
        elif strategy == "RSI_MEAN_REVERSION":
            signal = "NO SETUP"
        elif direction == "ABOVE":
            signal = "NO CROSS"
        else:
            signal = "BELOW"

        return {
            "bot": None,
            "pair": pair.replace("stock-", ""),
            "strategy": strategy or "SMA_CROSSOVER",
            "position": position,
            "sma_short": short,
            "sma_long": long,
            "spread_pct": num("spread_pct"),
            "direction": direction,
            "rsi": num("rsi"),
            "rsi_min": float(rsi_min) if rsi_min else None,
            "rsi_max": float(rsi_max) if rsi_max else None,
            "ema200": num("ema"),
            "price_vs_ema": None,
            "signal": signal,
            "blocked_by": blocked_by,
            "candle_time": last.get("candle_time", ""),
            "source": "signal_log",
            "raw": None,
        }

    @staticmethod
    def discover_bots():
        """
        Map pm2 process name -> symbol, from the environment each bot runs with.

        Crypto bots carry TRADING_PAIR, stock bots STOCK_TICKER. Stock symbols
        are prefixed "stock-" to match the signal log those bots write; the
        prefix is stripped for display.
        """
        try:
            from utils import registry
        except ImportError as e:
            logger.error(f"Could not import registry: {e}")
            return {}
        # Stock symbols are prefixed to match the signal log those bots write.
        return {b["name"]: (b["symbol"] if b["market"] == "crypto"
                            else f"stock-{b['symbol']}")
                for b in registry.bots()}

    def get_bot_signals(self):
        """
        Current decision state per bot.

        Crypto bots are read from their structured signal log; the stock bots
        do not write one, so they still fall back to parsing PM2 output.
        """
        # Discovered from PM2, not hardcoded. A fixed list means every bot
        # added with add_stock.py is invisible here until someone remembers to
        # edit this file — which is exactly what happened to the nine tickers
        # added on 2026-08-26.
        BOT_MAP = self.discover_bots()
        pm2_log_dir = os.path.expanduser("~/.pm2/logs")
        results = []

        # Parse indicator pattern:
        # SMA_short=X | SMA_long=Y | short is Z% ABOVE/BELOW long ... | RSI=R[min-max] | EMA200=E (price is above/below)
        ind_re = re.compile(
            r'SMA_short=(\S+).*?SMA_long=(\S+).*?short is ([\d.]+)% (ABOVE|BELOW) long.*?spread=([\d.]+)%'
            r'(?:.*?RSI=([\d.]+)\[([\d.]+)-([\d.]+)\])'
            r'(?:.*?EMA200=([\d.]+) \(price is (above|below)\))?',
            re.IGNORECASE
        )
        pos_re = re.compile(r'Position: (NONE|LONG)')

        env = self._env_values()

        for bot_name, pair in BOT_MAP.items():
            # Structured log first; PM2 scraping only where none exists.
            from_log = self.signal_from_log(pair, env)
            if from_log is not None:
                from_log["bot"] = bot_name
                results.append(from_log)
                continue

            log_path = os.path.join(pm2_log_dir, f"{bot_name}-out.log")
            err_path = os.path.join(pm2_log_dir, f"{bot_name}-error.log")

            indicator_line = None
            position = "NONE"
            lines = []

            # Check both out and error logs (some bots log to stderr)
            for lpath in [log_path, err_path]:
                if not os.path.exists(lpath):
                    continue
                try:
                    # Use tail to efficiently read the end of potentially massive log files
                    result = subprocess.run(["tail", "-n", "300", lpath], capture_output=True, text=True, errors="replace")
                    lines = result.stdout.splitlines()
                    for line in reversed(lines):
                        if "Indicators |" in line and indicator_line is None:
                            indicator_line = line
                        if "Position:" in line and ("LONG" in line or "NONE" in line):
                            m = pos_re.search(line)
                            if m:
                                position = m.group(1)
                        if indicator_line and position:
                            break
                except Exception:
                    pass
                if indicator_line:
                    break

            # The stock bots run whichever class .env selects. Deriving an
            # SMA-based signal for a mean-reversion bot is how SPY/QQQ/LPL
            # showed as ENTERING while sitting at RSI 50, nowhere near the
            # oversold level they actually trigger on.
            display_pair = pair.replace("stock-", "")
            prefix = display_pair.split("-")[0].upper()
            # Mirror stock_bot's own lookup: <TICKER>_STRATEGY, then
            # STOCK_STRATEGY. The coin -> profile -> base order used elsewhere
            # does not apply here and silently returned the wrong default.
            bot_strategy = (env.get(f"{prefix}_STRATEGY")
                            or env.get("STOCK_STRATEGY")
                            or "SMA_CROSSOVER").strip().upper()

            # A stock bot asleep outside market hours is not "waiting for a
            # setup" — it is not evaluating at all, and saying otherwise implies
            # activity that is not happening.
            market_closed = any("Market closed" in ln for ln in (lines or [])[-8:])

            entry = {
                "bot": bot_name,
                "pair": display_pair,
                "strategy": bot_strategy,
                "position": position,
                "sma_short": None, "sma_long": None,
                "spread_pct": None, "direction": None,
                "rsi": None, "rsi_min": None, "rsi_max": None,
                "ema200": None, "price_vs_ema": None,
                "signal": "BELOW",
                "blocked_by": "",
                "candle_time": "",
                "source": "pm2_log",
                "raw": indicator_line,
            }

            if indicator_line:
                m = ind_re.search(indicator_line)
                if m:
                    entry["sma_short"]   = float(m.group(1))
                    entry["sma_long"]    = float(m.group(2))
                    entry["spread_pct"]  = float(m.group(3))
                    entry["direction"]   = m.group(4).upper()   # ABOVE or BELOW
                    entry["rsi"]         = float(m.group(6)) if m.group(6) else None
                    entry["rsi_min"]     = float(m.group(7)) if m.group(7) else None
                    entry["rsi_max"]     = float(m.group(8)) if m.group(8) else None
                    entry["ema200"]      = float(m.group(9)) if m.group(9) else None
                    entry["price_vs_ema"]= m.group(10).upper() if m.group(10) else None

                    # Derive signal readiness
                    rsi_ok = True
                    if entry["rsi"] and entry["rsi_min"] and entry["rsi_max"]:
                        rsi_ok = entry["rsi_min"] <= entry["rsi"] <= entry["rsi_max"]
                    ema_ok = entry["price_vs_ema"] != "BELOW" if entry["price_vs_ema"] else True

                    if position == "LONG":
                        entry["signal"] = "IN TRADE"
                    elif market_closed:
                        entry["signal"] = "CLOSED"
                    elif bot_strategy == "RSI_MEAN_REVERSION":
                        # This path cannot see the RSI crossing, only the level,
                        # so it reports no setup rather than guessing.
                        entry["signal"] = "NO SETUP"
                    elif entry["direction"] == "ABOVE" and rsi_ok and ema_ok:
                        entry["signal"] = "ENTERING"
                    elif entry["direction"] == "ABOVE":
                        entry["signal"] = "BLOCKED"
                    else:
                        entry["signal"] = "BELOW"

            results.append(entry)

        return results

    def get_pm2_status(self):
        try:
            result = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                processes = json.loads(result.stdout)
                bots_list = []
                for p in processes:
                    name = p.get("name", "")
                    if name.endswith("-bot") or name == "dashboard":
                        env = p.get("pm2_env", {})
                        dry_run_val = env.get("DRY_RUN", "True")
                        args = env.get("args", [])
                        
                        is_dry = True
                        if "--live" in args:
                            is_dry = False
                        elif str(dry_run_val).strip().lower() in ("false", "0", "f", "no"):
                            is_dry = False
                            
                        status = env.get("status", "unknown")
                        monit = p.get("monit", {})
                        memory = monit.get("memory", 0) / (1024 * 1024) # MB
                        cpu = monit.get("cpu", 0)
                        restarts = env.get("restart_time", 0)
                        
                        bots_list.append({
                            "name": name,
                            "status": status.upper(),
                            "mode": "Simulated" if is_dry else "Live",
                            "memory": f"{memory:.1f} MB",
                            "cpu": f"{cpu}%",
                            "restarts": restarts
                        })
                return bots_list
        except Exception as e:
            logger.error(f"Error fetching PM2 status: {e}")
        return []

    @staticmethod
    def load_screens(log_dir=None):
        """
        Candidate screens written by screen_candidates.py, one per market.

        Ranked on whether a symbol suits the strategy — does it reach oversold,
        and does it then recover — rather than on recent returns. The two
        markets are screened under their own cost model and are never merged:
        equities are commission-free while Coinbase costs ~1.2% a round trip,
        which alone decides whether a strategy is viable.
        """
        if log_dir is None:
            log_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "logs")
        screens = {}
        for market in ("stocks", "crypto"):
            path = os.path.join(log_dir, f"screen_{market}.json")
            try:
                with open(path) as handle:
                    payload = json.load(handle)
            except (OSError, ValueError):
                continue
            rows = payload.get("results", [])
            screens[market] = {
                "strategy": payload.get("strategy", ""),
                "timeframe": payload.get("timeframe", ""),
                "cost_model": payload.get("cost_model", ""),
                "universe_size": payload.get("universe_size", len(rows)),
                "age_seconds": max(0.0, time.time() - payload.get("generated_at", 0)),
                "counts": {
                    v: sum(1 for r in rows if r.get("verdict") == v)
                    for v in ("check", "weak", "skip", "avoid")
                },
                # Only the actionable ends: what to test, and what to stay away
                # from. The middle is noise at these sample sizes.
                "check": [r for r in rows if r.get("verdict") == "check"][:8],
                "avoid": [r for r in rows if r.get("verdict") == "avoid"][:6],
            }
        return screens

    def build_signal_funnel(self, window_days=7, log_dir=None, now=None):
        """
        Where crossovers go: seen, rejected by which filter, or taken.

        completed_trades only records entries that happened, so a filter that
        has quietly stopped the bot trading is invisible there — the ledger just
        shows nothing new, which looks identical to a quiet market. The signal
        log records every candle decision including blocked_by, so tightening a
        filter too far becomes visible instead of silent.
        """
        if log_dir is None:
            log_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "logs")
        now = time.time() if now is None else now
        cutoff = now - window_days * 86400

        funnel = {
            "window_days": window_days,
            "candles": 0,
            "crossovers": 0,
            "taken": 0,
            "blocked": {},
            "last_crossover": None,
            "last_entry": None,
            "pairs": 0,
        }

        from datetime import datetime, timezone

        def parsed(stamp):
            try:
                return datetime.fromisoformat(
                    (stamp or "").replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                return None

        paths = glob.glob(os.path.join(log_dir, "signal_log_*.csv"))
        for path in paths:
            try:
                with open(path, newline="") as handle:
                    rows = list(csv.DictReader(handle))
            except (OSError, csv.Error) as e:
                logger.error(f"Error reading {os.path.basename(path)}: {e}")
                continue
            if rows:
                funnel["pairs"] += 1

            for row in rows:
                when = parsed(row.get("timestamp"))
                if when is not None and when < cutoff:
                    continue
                funnel["candles"] += 1

                if row.get("raw_signal") != "BUY":
                    continue
                funnel["crossovers"] += 1
                if when is not None:
                    funnel["last_crossover"] = max(
                        funnel["last_crossover"] or 0, when)

                reason = row.get("blocked_by") or ""
                if reason:
                    funnel["blocked"][reason] = funnel["blocked"].get(reason, 0) + 1
                elif row.get("final_signal") == "BUY":
                    funnel["taken"] += 1
                    if when is not None:
                        funnel["last_entry"] = max(funnel["last_entry"] or 0, when)

        for key in ("last_crossover", "last_entry"):
            funnel[f"{key}_age"] = (
                now - funnel[key] if funnel[key] else None)
            funnel.pop(key)

        funnel["blocked_total"] = sum(funnel["blocked"].values())
        funnel["blocked"] = sorted(
            ({"reason": r, "count": c} for r, c in funnel["blocked"].items()),
            key=lambda item: -item["count"],
        )
        return funnel

    @staticmethod
    def build_graduation(sim_trades):
        """
        Per-coin progress toward live-trading eligibility.

        Evaluated by utils.graduation, the same code the nightly report renders
        and the learner's promotion gate enforces. Previously this counted four
        criteria while the emailed report scored five, so the same coin showed
        two different readiness figures.
        """
        try:
            from utils.graduation import evaluate, MAX_SCORE
        except ImportError:
            logger.warning("Could not import utils.graduation")
            return []

        from utils import benchmark

        by_pair = {}
        for t in sim_trades:
            by_pair.setdefault(t["pair"], []).append(t)

        coins = []
        for pair, rows in by_pair.items():
            pnls = [r["pnl_usd"] for r in rows]
            # Computed from the ledger's own entry/exit prices, so the
            # benchmark criterion is actually evaluated rather than failing by
            # default for want of a price feed.
            excess = benchmark.excess_from_trades(rows, pnls)
            result = evaluate(pnls, benchmark_excess=excess)
            coins.append({
                "pair": pair,
                "trades": result["stats"]["trades"],
                "checks": [
                    {"label": c["label"], "passed": c["passed"], "detail": c["detail"]}
                    for c in result["criteria"]
                ],
                "passed": result["passed"],
                "total": result["total"],
                "score": result["score"],
                "max_score": MAX_SCORE,
                "ready": result["ready"],
            })

        # Closest to eligible first — that is the one worth watching.
        coins.sort(key=lambda c: (-c["score"], -c["trades"]))
        return coins

    @staticmethod
    def _cached_correlations():
        """
        Correlations from logs/correlations.json, written by
        analyze_correlation.py.

        Computing this live would mean fetching 400 days of bars for every
        symbol on every dashboard refresh. The age is carried through so a
        stale figure can be labelled rather than presented as current — the
        same mistake the 34-day-old scanner file caused.
        """
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "logs", "correlations.json")
        try:
            with open(path) as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return {}
        age = max(0.0, time.time() - payload.get("generated_at", 0))
        markets = payload.get("markets", {})
        for value in markets.values():
            value["age_seconds"] = age
        return markets

    @staticmethod
    def build_pooled_graduation():
        """
        Progress toward live, pooled across every symbol in a market.

        The per-symbol view answers "is WMT ready", which needs 20 trades on
        WMT alone and takes months. But the thing being tested is a STRATEGY —
        RSI mean reversion on equities — not any one ticker, so the honest unit
        of evidence is every trade that strategy has made. Twelve tickers reach
        20 pooled trades roughly twelve times faster, and it is the question
        actually being asked.

        Going live should then use a BASKET of qualifying symbols. Picking the
        single best of twelve after the fact is selection, not evidence, and it
        is how a backtest that means nothing gets promoted.

        Evaluated by utils.graduation — the same five criteria, the same code
        the promotion gate enforces.
        """
        try:
            from utils.graduation import evaluate, MAX_SCORE, MIN_TRADES
        except ImportError:
            logger.warning("Could not import utils.graduation")
            return []

        root = os.path.dirname(os.path.abspath(__file__))
        markets = {
            "stocks": glob.glob(os.path.join(root, "completed_trades_stock_*_paper.csv")),
            "crypto": glob.glob(os.path.join(root, "completed_trades_*-usdc_dry.csv")),
        }

        from utils import benchmark, evidence

        out = []
        for market, paths in markets.items():
            pnls, symbols, rows_by_symbol = [], set(), {}
            for path in paths:
                try:
                    with open(path, newline="") as handle:
                        for row in csv.DictReader(handle):
                            # A repeated header line appears in some files; it
                            # parses as a row whose pnl is the literal string.
                            if not row.get("exit_time") or row.get("exit_time") == "exit_time":
                                continue
                            try:
                                pnls.append(float(row.get("pnl_usd") or 0.0))
                            except (TypeError, ValueError):
                                continue
                            sym = (row.get("ticker") or row.get("pair") or "").upper()
                            symbols.add(sym)
                            rows_by_symbol.setdefault(sym, []).append(row)
                except OSError:
                    continue

            if not pnls:
                out.append({"market": market, "trades": 0, "symbols": len(paths),
                            "needed": MIN_TRADES, "checks": [], "ready": False,
                            "score": 0, "max_score": MAX_SCORE, "passed": 0, "total": 5})
                continue

            # Equal-weighted buy-and-hold across the symbols traded, against
            # the pooled strategy return: the honest alternative to running
            # any of these bots is holding the same basket.
            holds = [h for h in (benchmark.from_trades(r)
                                 for r in rows_by_symbol.values()) if h is not None]
            excess = None
            if holds:
                strat = benchmark.strategy_return(pnls)
                if strat is not None:
                    excess = strat - (sum(holds) / len(holds))

            result = evaluate(pnls, benchmark_excess=excess)

            # Correlated symbols do not each contribute a full trade's worth of
            # evidence. Correlation comes from PRICE returns, computed by
            # analyze_correlation.py and cached: trade outcomes keyed by exit
            # time almost never overlap between symbols, so a trade-level
            # correlation is undefined and would report perfect independence
            # purely from having no shared observations.
            sharpe = result["stats"]["sharpe"]
            ev = {
                "symbols": len(symbols),
                "pooled_trades": result["stats"]["trades"],
                "measured": False,
                "effective_symbols": None,
                "effective_trades": None,
                "mean_correlation": None,
                "trades_needed": evidence.trades_needed(
                    sharpe if sharpe not in (float("inf"), float("-inf")) else None),
            }
            cached = DashboardHandler._cached_correlations().get(market)
            if cached and cached.get("effective_symbols"):
                k = cached["symbols"] or len(symbols)
                ev.update({
                    "measured": True,
                    # Report the universe the correlation was measured over,
                    # not just the symbols that happen to have traded, or the
                    # ratio reads as nonsense like "4.9 of 2".
                    "symbols": k,
                    "traded_symbols": len(symbols),
                    "effective_symbols": cached["effective_symbols"],
                    "mean_correlation": cached["mean_correlation"],
                    "effective_trades": evidence.effective_trades(
                        result["stats"]["trades"], k, cached["effective_symbols"]),
                    "correlation_age_seconds": cached.get("age_seconds"),
                })

            out.append({
                "market": market,
                "trades": result["stats"]["trades"],
                "symbols": len(symbols) or len(paths),
                "needed": MIN_TRADES,
                "evidence": ev,
                "checks": [{"label": c["label"], "passed": c["passed"],
                            "detail": c["detail"]} for c in result["criteria"]],
                "passed": result["passed"],
                "total": result["total"],
                "score": result["score"],
                "max_score": MAX_SCORE,
                "ready": result["ready"],
            })
        return out

    def build_analytics(self, trades):
        """
        Derive the views that answer "is the strategy working", as opposed to
        "what is it doing right now".

        Realized P&L and fees shown side by side hide the fact that matters:
        whether the strategy has a positive edge *before* costs. A strategy can
        pick direction well and still lose, if the round-trip fee exceeds the
        move it captures. These aggregates make that visible.

        Scoped to simulated crypto trades. That is where the sample lives, and
        the stock bots trade commission-free on another venue — folding them in
        would dilute exactly the fee drag this is meant to expose.
        """
        sim = [t for t in trades
               if t["mode"] == "Simulated" and t.get("asset_class") == "crypto"]
        analytics = {
            "trade_count": len(sim),
            "fee_floor_pct": None,
            "waterfall": None,
            "exit_reasons": [],
            "entry_quality": None,
            "equity_curve": [],
            "graduation": [],
        }
        if not sim:
            return analytics

        net = sum(t["pnl_usd"] for t in sim)
        fees = sum(t["fees_usd"] for t in sim)
        wins = sum(1 for t in sim if t["pnl_usd"] > 0)
        analytics["waterfall"] = {
            "gross": net + fees,
            "fees": fees,
            "net": net,
            "win_rate": 100.0 * wins / len(sim),
        }

        # The round-trip cost is whatever gross-to-net actually gave up, rather
        # than a configured rate that may not match what was charged.
        drags = [t["gross_pct"] - t["net_pct"] for t in sim if t["has_excursion"]]
        fee_floor = sum(drags) / len(drags) if drags else None
        analytics["fee_floor_pct"] = fee_floor

        by_reason = {}
        for trade in sim:
            reason = trade["exit_reason"] or "Unknown"
            bucket = by_reason.setdefault(reason, {"count": 0, "net_pct": 0.0, "pnl": 0.0})
            bucket["count"] += 1
            bucket["net_pct"] += trade["net_pct"]
            bucket["pnl"] += trade["pnl_usd"]
        analytics["exit_reasons"] = sorted(
            (
                {
                    "reason": reason,
                    "count": b["count"],
                    "share": 100.0 * b["count"] / len(sim),
                    "avg_net_pct": b["net_pct"] / b["count"],
                    "total_pnl": b["pnl"],
                }
                for reason, b in by_reason.items()
            ),
            key=lambda item: -item["count"],
        )

        # How many entries ever travelled far enough to pay for themselves.
        measured = [t for t in sim if t["has_excursion"]]
        if measured and fee_floor is not None:
            cleared = sum(1 for t in measured if t["mfe_pct"] >= fee_floor)
            analytics["entry_quality"] = {
                "measured": len(measured),
                "cleared_fee_floor": cleared,
                "cleared_pct": 100.0 * cleared / len(measured),
                "avg_mfe": sum(t["mfe_pct"] for t in measured) / len(measured),
                "avg_mae": sum(t["mae_pct"] for t in measured) / len(measured),
            }

        analytics["graduation"] = DashboardHandler.build_graduation(sim)
        analytics["pooled_graduation"] = DashboardHandler.build_pooled_graduation()

        # Oldest-first cumulative net, for the only view with a time axis.
        chronological = sorted(
            (t for t in sim if t["exit_time"]), key=lambda t: t["exit_time"]
        )
        running = 0.0
        for trade in chronological:
            running += trade["pnl_usd"]
            analytics["equity_curve"].append({
                "exit_time": trade["exit_time"],
                "cumulative_pnl": running,
                "pair": trade["pair"],
            })

        return analytics

    def get_dashboard_data(self):
        project_root = os.path.dirname(os.path.abspath(__file__))
        # Fleet health. The failures this catches — a bot trading the wrong
        # instrument, or several sharing one state file — are invisible in
        # every other number on this page: they present as an absence of
        # trades, which is what a quiet market also looks like. Published so
        # the UI can say something is wrong instead of showing a fleet that
        # merely appears idle.
        try:
            from utils import registry
            fleet_problems = registry.audit()
        except Exception as e:
            logger.error(f"Fleet audit failed: {e}")
            fleet_problems = [f"could not audit the fleet: {e}"]

        data = {
            "timestamp": datetime_now_iso(),
            "scanner": {},
            "active_positions": [],
            "fleet_problems": fleet_problems,
            "bots": self.get_pm2_status(),
            "performance": {
                "live_realized_pnl": 0.0,
                "live_fees": 0.0,
                "sim_realized_pnl": 0.0,
                "sim_fees": 0.0
            }
        }

        # 1. Read scanner data.
        # Screener display only — position pricing goes through PriceResolver.
        # This file is written on the scanner's own schedule and can be weeks
        # old, so its age is published and the UI labels it rather than
        # presenting it as current.
        metrics_path = os.path.join(project_root, 'logs/market_metrics.json')
        if os.path.exists(metrics_path):
            try:
                with open(metrics_path, 'r') as f:
                    data["scanner"] = json.load(f)
                data["scanner_age_seconds"] = time.time() - os.path.getmtime(metrics_path)
            except Exception as e:
                logger.error(f"Error loading market metrics JSON: {e}")

        # 2. Read active positions from state files in root directory
        for filename in os.listdir(project_root):
            if filename.startswith("state_") and filename.endswith(".json"):
                state_path = os.path.join(project_root, filename)
                try:
                    with open(state_path, 'r') as f:
                        state = json.load(f)
                    
                    if state.get("position") == "LONG":
                        raw_coin = filename.replace("state_", "").replace(".json", "").upper()
                        is_dry = raw_coin.endswith("_DRY") or state.get("dry_run", True)
                        coin = raw_coin.replace("_DRY", "")
                        # Split pair representation (e.g. BTC-USDC -> BTC)
                        coin_symbol = coin.split("-")[0]
                        
                        entry_price = float(state.get("entry_price", 0.0))
                        holdings = float(state.get("holdings_base", 0.0))

                        current_price, price_source, price_age = (
                            self.price_resolver.get(coin, entry_price)
                        )

                        unrealized_pnl = 0.0
                        if entry_price > 0:
                            unrealized_pnl = (current_price - entry_price) * holdings
                            # Subtract entry fee
                            entry_fee = float(state.get("entry_fee_usd", 0.0))
                            unrealized_pnl -= entry_fee

                        data["active_positions"].append({
                            "coin": coin,
                            "mode": "Simulated" if is_dry else "Live",
                            "holdings": holdings,
                            "entry_price": entry_price,
                            "current_price": current_price,
                            "unrealized_pnl": unrealized_pnl,
                            "entry_time": state.get("entry_time", ""),
                            # Provenance travels with the number so the UI can
                            # flag a P&L that should not be read as current.
                            "price_source": price_source,
                            "price_age_seconds": price_age,
                            "price_stale": (
                                price_source == "entry"
                                or (price_age is not None
                                    and price_age > PRICE_STALE_AFTER_SECONDS)
                            ),
                        })
                except Exception as e:
                    logger.error(f"Error reading state file {filename}: {e}")

        # 3. Read completed trades CSV files to populate performance cards
        def read_pnl_csv_files(files):
            pnl = 0.0
            fees = 0.0
            for path in files:
                if os.path.exists(path):
                    try:
                        with open(path, 'r') as f:
                            reader = csv.DictReader(f)
                            for row in reader:
                                try:
                                    pnl += float(row.get("pnl_usd", 0.0))
                                    fees += float(row.get("fees_usd", 0.0))
                                except (ValueError, TypeError):
                                    pass
                    except Exception as e:
                        logger.error(f"Error reading performance CSV {os.path.basename(path)}: {e}")
            return pnl, fees

        sim_files = glob.glob(os.path.join(project_root, "completed_trades_*_dry.csv"))
        sim_files.extend(glob.glob(os.path.join(project_root, "completed_trades_*_paper.csv")))
        shared_dry = os.path.join(project_root, "completed_trades_dry.csv")
        if os.path.exists(shared_dry) and shared_dry not in sim_files:
            sim_files.append(shared_dry)

        live_files = glob.glob(os.path.join(project_root, "completed_trades_*.csv"))
        live_files = [f for f in live_files if not f.endswith("_dry.csv") and not f.endswith("_bak.csv") and not f.endswith("_paper.csv")]
        shared_live = os.path.join(project_root, "completed_trades.csv")
        if os.path.exists(shared_live) and shared_live not in live_files:
            live_files.append(shared_live)

        live_pnl, live_fees = read_pnl_csv_files(live_files)
        sim_pnl, sim_fees = read_pnl_csv_files(sim_files)
        
        data["performance"]["live_realized_pnl"] = live_pnl
        data["performance"]["live_fees"] = live_fees
        data["performance"]["sim_realized_pnl"] = sim_pnl
        data["performance"]["sim_fees"] = sim_fees

        # 4. Read completed trades history to populate a dashboard table showing "what and why"
        completed_list = []
        def read_completed_trades_files(files, mode):
            for path in files:
                # Stock bots trade commission-free on a different venue, so
                # their trades must not be averaged into crypto fee analysis.
                asset_class = "stock" if path.endswith("_paper.csv") else "crypto"
                if os.path.exists(path):
                    try:
                        with open(path, 'r') as f:
                            reader = csv.DictReader(f)
                            for row in reader:
                                try:
                                    entry_p = float(row.get("entry_price") or 0.0)
                                except ValueError:
                                    entry_p = 0.0
                                try:
                                    exit_p = float(row.get("exit_price") or 0.0)
                                except ValueError:
                                    exit_p = 0.0
                                try:
                                    pnl = float(row.get("pnl_usd") or 0.0)
                                except ValueError:
                                    pnl = 0.0
                                try:
                                    fees = float(row.get("fees_usd") or 0.0)
                                except ValueError:
                                    fees = 0.0
                                
                                def num(key):
                                    """Excursion columns are absent on the stock CSVs."""
                                    try:
                                        return float(row.get(key) or 0.0)
                                    except (ValueError, TypeError):
                                        return 0.0

                                completed_list.append({
                                    "pair": row.get("pair") or row.get("ticker", "UNKNOWN"),
                                    "mode": mode,
                                    "entry_time": row.get("entry_time", ""),
                                    "exit_time": row.get("exit_time", ""),
                                    "entry_price": entry_p,
                                    "exit_price": exit_p,
                                    "pnl_usd": pnl,
                                    "fees_usd": fees,
                                    "entry_reason": row.get("entry_reason", ""),
                                    "exit_reason": row.get("exit_reason", ""),
                                    "gross_pct": num("gross_pct"),
                                    "net_pct": num("net_pct"),
                                    "mfe_pct": num("mfe_pct"),
                                    "mae_pct": num("mae_pct"),
                                    "has_excursion": bool(row.get("mfe_pct")),
                                    "asset_class": asset_class,
                                })
                    except Exception as e:
                        logger.error(f"Error reading completed trades list CSV {os.path.basename(path)}: {e}")

        read_completed_trades_files(live_files, "Live")
        read_completed_trades_files(sim_files, "Simulated")
        
        # Sort by exit_time descending
        completed_list.sort(key=lambda x: x.get("exit_time", ""), reverse=True)
        # Send last 30 completed trades
        data["completed_trades"] = completed_list[:30]

        # 5. Derived analytics — the views that answer "is this working?"
        data["analytics"] = self.build_analytics(completed_list)
        data["signal_funnel"] = self.build_signal_funnel()
        data["screens"] = self.load_screens()

        # 6. Read auto-learner report
        best_params_path = os.path.join(project_root, "logs", "best_params.json")
        data["auto_learner_report"] = None
        if os.path.exists(best_params_path):
            try:
                with open(best_params_path, 'r') as f:
                    data["auto_learner_report"] = json.load(f)
            except Exception as e:
                print(f"Error reading auto-learner report: {e}")


        return data

def datetime_now_iso():
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"

if __name__ == "__main__":
    # Change working directory to project root
    project_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_root)
    
    # Expose server
    handler = DashboardHandler
    # Enable socket reuse
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"==================================================")
        print(f"📊 Coinbase System Bot Web Dashboard running")
        print(f"🔗 Local access: http://localhost:{PORT}")
        print(f"🔗 Network access: http://<your_server_ip>:{PORT}")
        print(f"==================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
