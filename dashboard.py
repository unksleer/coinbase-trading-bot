#!/usr/bin/env python3
import os
import re
import json
import http.server
import socketserver
import csv
import logging
import subprocess
import glob


PORT = 8080

logger = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO)

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Serve static files from the 'dashboard' folder
        project_root = os.path.dirname(os.path.abspath(__file__))
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

    def get_bot_signals(self):
        """
        For each known bot, read the last 200 lines of its PM2 out log
        and extract the most recent Indicators line.
        Returns a list of dicts with parsed SMA, RSI, EMA, spread, signal info.
        """
        BOT_MAP = {
            "btc-bot":    "BTC-USDC",
            "eth-bot":    "ETH-USDC",
            "sol-bot":    "SOL-USDC",
            "ada-bot":    "ADA-USDC",
            "link-bot":   "LINK-USDC",
            "near-bot":   "NEAR-USDC",
            "sui-bot":    "SUI-USDC",
            "xrp-bot":    "XRP-USDC",
            "render-bot": "RENDER-USDC",
            # Stock bots (Alpaca)
            "spy-bot":    "SPY",
            "qqq-bot":    "QQQ",
            "lpl-bot":    "LPL",
        }
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

        for bot_name, pair in BOT_MAP.items():
            log_path = os.path.join(pm2_log_dir, f"{bot_name}-out.log")
            err_path = os.path.join(pm2_log_dir, f"{bot_name}-error.log")

            indicator_line = None
            position = "NONE"

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

            entry = {
                "bot": bot_name,
                "pair": pair,
                "position": position,
                "sma_short": None, "sma_long": None,
                "spread_pct": None, "direction": None,
                "rsi": None, "rsi_min": None, "rsi_max": None,
                "ema200": None, "price_vs_ema": None,
                "signal": "WAITING",
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
                    elif entry["direction"] == "ABOVE" and rsi_ok and ema_ok:
                        entry["signal"] = "READY"   # crossover already happened, filters pass
                    elif entry["direction"] == "ABOVE":
                        entry["signal"] = "CLOSE"   # SMA bullish but filter blocking
                    else:
                        entry["signal"] = "WAITING"  # SMA bearish

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

    def get_dashboard_data(self):
        project_root = os.path.dirname(os.path.abspath(__file__))
        data = {
            "timestamp": datetime_now_iso(),
            "scanner": {},
            "active_positions": [],
            "bots": self.get_pm2_status(),
            "performance": {
                "live_realized_pnl": 0.0,
                "live_fees": 0.0,
                "sim_realized_pnl": 0.0,
                "sim_fees": 0.0
            }
        }

        # 1. Read scanner data
        metrics_path = os.path.join(project_root, 'logs/market_metrics.json')
        scanner_prices = {}
        if os.path.exists(metrics_path):
            try:
                with open(metrics_path, 'r') as f:
                    data["scanner"] = json.load(f)
                
                # Create helper lookup map of coin prices from scanner results
                for r in data["scanner"].get("results", []):
                    coin = r["Coin"]
                    scanner_prices[coin] = float(r["Price"])
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
                        
                        # Lookup current price from scanner, default to entry_price if not scanned
                        current_price = scanner_prices.get(coin_symbol, entry_price)
                        
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
                            "entry_time": state.get("entry_time", "")
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
                                    "exit_reason": row.get("exit_reason", "")
                                })
                    except Exception as e:
                        logger.error(f"Error reading completed trades list CSV {os.path.basename(path)}: {e}")

        read_completed_trades_files(live_files, "Live")
        read_completed_trades_files(sim_files, "Simulated")
        
        # Sort by exit_time descending
        completed_list.sort(key=lambda x: x.get("exit_time", ""), reverse=True)
        # Send last 30 completed trades
        data["completed_trades"] = completed_list[:30]

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
