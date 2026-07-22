#!/usr/bin/env python3
import os
import json
import http.server
import socketserver
import csv
import logging
import subprocess

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
            
        return super().do_GET()

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
        def read_pnl_csv(filename):
            path = os.path.join(project_root, filename)
            pnl = 0.0
            fees = 0.0
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
                    logger.error(f"Error reading performance CSV {filename}: {e}")
            return pnl, fees

        live_pnl, live_fees = read_pnl_csv("completed_trades.csv")
        sim_pnl, sim_fees = read_pnl_csv("completed_trades_dry.csv")
        
        data["performance"]["live_realized_pnl"] = live_pnl
        data["performance"]["live_fees"] = live_fees
        data["performance"]["sim_realized_pnl"] = sim_pnl
        data["performance"]["sim_fees"] = sim_fees

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
