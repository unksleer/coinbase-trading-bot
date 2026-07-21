import os
import sys
import json
import smtplib
import time
import requests
import subprocess
import pandas as pd
from datetime import datetime
from ssl import create_default_context
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Add project root to path
PROJECT_ROOT = "/home/leeunks/coinbase-trading-bot"
sys.path.append(PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# SMTP configuration
SMTP_ENABLED = os.getenv("EMAIL_NOTIFICATIONS_ENABLED", "False").lower() in ("true", "1", "yes")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
NOTIFICATION_EMAIL = os.getenv("NOTIFICATION_EMAIL", SMTP_USER)

def get_live_price(product_id: str) -> float:
    # Strip any bot suffixes (like -HYPER or -ATR) for Coinbase API lookup
    base_pair = product_id.split('-')
    clean_pair = f"{base_pair[0]}-USDC" if len(base_pair) > 1 else "BTC-USDC"
    
    url = f"https://api.exchange.coinbase.com/products/{clean_pair}/ticker"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return float(response.json().get("price", 0.0))
    except Exception as e:
        print(f"Error fetching ticker for {clean_pair}: {e}")
        return 0.0

def send_pnl_email(subject: str, html_content: str):
    if not SMTP_ENABLED:
        print("Email notifications are disabled in .env. Skipping PnL report email.")
        return
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print("SMTP credentials are incomplete in .env. Skipping PnL report email.")
        return
        
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Coinbase Bot Reporter <{SMTP_USER}>"
    msg["To"] = NOTIFICATION_EMAIL
    
    part_html = MIMEText(html_content, "html")
    msg.attach(part_html)
    
    try:
        context = create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, NOTIFICATION_EMAIL, msg.as_string())
        print("PnL Report email sent successfully!")
    except Exception as e:
        print(f"Failed to send PnL email: {e}")

def main():
    print(f"[{datetime.now().isoformat()}] Generating Daily PnL Statement...")
    
    # 1. Fetch all PM2 processes
    try:
        # Prepend Node/NVM bin folder to PATH so subprocess can resolve node and pm2 under restricted cron environments
        env = os.environ.copy()
        nvm_path = "/home/leeunks/.nvm/versions/node/v20.20.2/bin"
        env["PATH"] = f"{nvm_path}:{env.get('PATH', '')}"
        
        result = subprocess.run(['pm2', 'jlist'], capture_output=True, text=True, check=True, env=env)
        pm2_list = json.loads(result.stdout)
    except Exception as e:
        print(f"Error listing PM2 processes: {e}")
        return

    # 2. Load completed trades CSV for realized PnL
    csv_path = os.path.join(PROJECT_ROOT, "completed_trades.csv")
    df_completed = pd.DataFrame()
    if os.path.exists(csv_path):
        try:
            df_completed = pd.read_csv(csv_path)
        except Exception as e:
            print(f"Error reading completed_trades.csv: {e}")

    bot_reports = []
    
    # 3. Process each online trading bot
    for p in pm2_list:
        name = p.get("name")
        env = p.get("pm2_env", {})
        status = env.get("status", p.get("status"))
        
        # Skip system or non-trading processes
        if status != "online" or name == "hgtv-automation":
            continue
            
        pair = env.get("TRADING_PAIR", "SOL-USDC")
        state_file_name = env.get("STATE_FILE", "")
        dry_run = env.get("DRY_RUN", "True").lower() in ("true", "1", "t", "yes")
        is_dry_run_str = "Simulated" if dry_run else "Live"
        
        # Load state file
        state_pnl_usd = 0.0
        position = "NONE"
        entry_price = 0.0
        holdings = 0.0
        unrealized_pnl = 0.0
        current_price = 0.0
        
        if state_file_name:
            state_path = os.path.join(PROJECT_ROOT, state_file_name)
            if os.path.exists(state_path):
                try:
                    with open(state_path, 'r') as sf:
                        state_data = json.load(sf)
                    position = state_data.get("position", "NONE")
                    entry_price = float(state_data.get("entry_price", 0.0))
                    holdings = float(state_data.get("holdings_base", 0.0))
                    
                    if position == "LONG" and holdings > 0.0:
                        current_price = get_live_price(pair)
                        if current_price > 0.0:
                            # Gross unrealized PnL
                            unrealized_pnl = (current_price - entry_price) * holdings
                            # Subtract estimated entry fee for net unrealized
                            entry_fee = float(state_data.get("entry_fee_usd", 0.0))
                            unrealized_pnl -= entry_fee
                except Exception as e:
                    print(f"Error reading state file {state_file_name}: {e}")
                    
        # Calculate realized PnL from completed_trades.csv
        realized_pnl = 0.0
        trades_count = 0
        if not df_completed.empty:
            # Map bot names to the CSV pair representation
            # Simulated bots write with -HYPER or -ATR suffix
            csv_pair_name = pair
            if dry_run:
                if "hyper" in name:
                    csv_pair_name = f"{pair}-HYPER"
                elif "atr" in name:
                    csv_pair_name = f"{pair}-ATR"
            
            bot_trades = df_completed[df_completed['pair'] == csv_pair_name]
            trades_count = len(bot_trades)
            if 'pnl_usd' in bot_trades.columns:
                realized_pnl = float(bot_trades['pnl_usd'].sum())
            else:
                # Fallback calculation if pnl_usd column is somehow missing
                realized_pnl = float((bot_trades['net_pct'] / 100.0 * 10.0).sum()) # Assume $10 base capital fallback
                
        bot_reports.append({
            "name": name,
            "pair": pair,
            "mode": is_dry_run_str,
            "position": position,
            "entry": entry_price,
            "current": current_price,
            "holdings": holdings,
            "trades_count": trades_count,
            "realized": realized_pnl,
            "unrealized": unrealized_pnl,
            "total": realized_pnl + unrealized_pnl
        })
        time.sleep(0.1) # Safe API rate limit spacing

    if not bot_reports:
        print("No active trading bots to report.")
        return

    # Build PnL HTML Table rows
    table_rows = ""
    total_live_pnl = 0.0
    total_sim_pnl = 0.0
    
    for r in bot_reports:
        color_r = "#34d399" if r["realized"] >= 0 else "#f87171"
        color_u = "#34d399" if r["unrealized"] >= 0 else "#f87171"
        color_t = "#34d399" if r["total"] >= 0 else "#f87171"
        
        pos_badge = f"""<span style="padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold; background-color: rgba(16, 185, 129, 0.2); color: #34d399;">LONG</span>""" if r["position"] == "LONG" else f"""<span style="padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold; background-color: rgba(156, 163, 175, 0.2); color: #9ca3af;">NONE</span>"""
        mode_badge = f"""<span style="padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold; background-color: rgba(239, 68, 68, 0.2); color: #f87171;">LIVE</span>""" if r["mode"] == "Live" else f"""<span style="padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold; background-color: rgba(59, 130, 246, 0.2); color: #60a5fa;">SIM</span>"""
        
        # Accumulate totals
        if r["mode"] == "Live":
            total_live_pnl += r["total"]
        else:
            total_sim_pnl += r["total"]
            
        entry_str = f"${r['entry']:,.2f}" if r["entry"] > 0 else "-"
        current_str = f"${r['current']:,.2f}" if r["current"] > 0 else "-"
        
        leveraged_val = r["entry"] * r["holdings"]
        leveraged_str = f"${leveraged_val:,.2f}" if r["position"] == "LONG" and leveraged_val > 0 else "-"
        
        table_rows += f"""
        <tr style="background-color: #111827;">
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #ffffff; font-weight: bold;">{r['name']}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; text-align: center;">{mode_badge}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; text-align: center; color: #cbd5e1;">{leveraged_str}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; text-align: center;">{pos_badge}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; text-align: right; color: #cbd5e1;">{entry_str}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; text-align: right; color: #cbd5e1;">{current_str}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; text-align: center; color: #9ca3af;">{r['trades_count']}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; text-align: right; color: {color_r}; font-weight: 500;">{r['realized']:+.2f} USD</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; text-align: right; color: {color_u}; font-weight: 500;">{r['unrealized']:+.2f} USD</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; text-align: right; color: {color_t}; font-weight: bold;">{r['total']:+.2f} USD</td>
        </tr>
        """
        
    live_color = "#34d399" if total_live_pnl >= 0 else "#f87171"
    sim_color = "#34d399" if total_sim_pnl >= 0 else "#f87171"
    
    # 4. Extract recent losses and exit reasons
    losses_html = ""
    if not df_completed.empty:
        pnl_col = 'pnl_usd' if 'pnl_usd' in df_completed.columns else 'net_pct'
        df_losses = df_completed[df_completed[pnl_col] < 0.0]
        
        if not df_losses.empty:
            df_losses = df_losses.sort_values(by='exit_time', ascending=False)
            recent_losses = df_losses.head(5)
            
            losses_rows = ""
            for _, row in recent_losses.iterrows():
                exit_time_str = row['exit_time']
                try:
                    dt = datetime.strptime(exit_time_str.split('.')[0].replace('Z', ''), "%Y-%m-%dT%H:%M:%S")
                    date_display = dt.strftime("%b %d, %Y at %I:%M %p")
                except:
                    date_display = exit_time_str
                    
                pnl_val = float(row['pnl_usd']) if 'pnl_usd' in row and pd.notna(row['pnl_usd']) else 0.0
                pct_val = float(row['net_pct']) if 'net_pct' in row and pd.notna(row['net_pct']) else 0.0
                exit_reason_str = row['exit_reason']
                pair_name = row['pair']
                
                # Check what strategy settings were active
                sma_s = row.get('entry_sma_short')
                sma_l = row.get('entry_sma_long')
                if pd.notna(sma_s) and pd.notna(sma_l) and str(sma_s) != "" and str(sma_l) != "" and str(sma_s) != "nan" and str(sma_l) != "nan":
                    strat_display = f"SMA {int(float(sma_s))}/{int(float(sma_l))}"
                else:
                    strat_display = "SMA Crossover"
                
                # Compute leveraged amount mathematically: amount = pnl / (net_pct / 100.0)
                if pct_val != 0.0:
                    leveraged_amount = abs(pnl_val / (pct_val / 100.0))
                else:
                    leveraged_amount = 10.0 # Default fallback
                    
                setup_display = f"${leveraged_amount:,.2f} ({strat_display})"
                
                losses_rows += f"""
                <tr style="background-color: #111827;">
                    <td style="padding: 10px; border-bottom: 1px solid #1f2937; color: #ffffff; font-weight: bold;">{pair_name}</td>
                    <td style="padding: 10px; border-bottom: 1px solid #1f2937; color: #cbd5e1; font-size: 12px;">{date_display}</td>
                    <td style="padding: 10px; border-bottom: 1px solid #1f2937; color: #cbd5e1; font-size: 12px;">{setup_display}</td>
                    <td style="padding: 10px; border-bottom: 1px solid #1f2937; text-align: right; color: #f87171; font-weight: 500;">{pnl_val:+.2f} USD ({pct_val:+.2f}%)</td>
                    <td style="padding: 10px; border-bottom: 1px solid #1f2937; color: #f87171; font-weight: bold;">{exit_reason_str}</td>
                </tr>
                """
                
            losses_html = f"""
            <h3 style="margin-top: 30px; color: #ffffff; font-size: 16px; font-weight: 600; margin-bottom: 15px;">🚨 Recent Losses & Exit Reasons (Last 5)</h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; margin-bottom: 30px;">
                <thead>
                    <tr>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 10px; border-bottom: 2px solid #374151;">Pair</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 10px; border-bottom: 2px solid #374151;">Exit Date</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 10px; border-bottom: 2px solid #374151;">Leveraged Setup</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 10px; border-bottom: 2px solid #374151; text-align: right;">Loss (Net)</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 10px; border-bottom: 2px solid #374151;">Primary Cause / Reason</th>
                    </tr>
                </thead>
                <tbody>
                    {losses_rows}
                </tbody>
            </table>
            """
    
    date_str = datetime.now().strftime("%A, %B %d, %Y")
    
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #0b0f19; color: #f1f5f9; margin: 0; padding: 20px;">
    <div class="container" style="max-width: 800px; margin: 0 auto; background-color: #111827; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4); border: 1px solid #1f2937;">
        <div class="header" style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 35px 30px; text-align: center; border-bottom: 1px solid #1f2937;">
            <h1 style="margin: 0; font-size: 24px; font-weight: 700; color: #ffffff; letter-spacing: 0.5px;">Systematic Trading Bot</h1>
            <p style="margin: 5px 0 0 0; color: #94a3b8; font-size: 14px;">Daily Profit & Loss (PnL) Statement • {date_str}</p>
        </div>
        
        <div class="content" style="padding: 30px;">
            <!-- Summary Blocks -->
            <div style="display: flex; gap: 20px; margin-bottom: 30px;">
                <div style="flex: 1; padding: 20px; border-radius: 8px; background-color: #1e293b; border: 1px solid #334155; text-align: center;">
                    <div style="font-size: 12px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; font-weight: 600;">Combined Live Portfolio PnL</div>
                    <div style="font-size: 24px; font-weight: bold; color: {live_color}; margin-top: 8px;">{total_live_pnl:+.2f} USD</div>
                </div>
                <div style="flex: 1; padding: 20px; border-radius: 8px; background-color: #1e293b; border: 1px solid #334155; text-align: center;">
                    <div style="font-size: 12px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; font-weight: 600;">Combined Simulated PnL</div>
                    <div style="font-size: 24px; font-weight: bold; color: {sim_color}; margin-top: 8px;">{total_sim_pnl:+.2f} USD</div>
                </div>
            </div>
            
            <h3 style="margin-top: 0; color: #ffffff; font-size: 16px; font-weight: 600; margin-bottom: 15px;">Asset Performance Breakdown</h3>
            
            <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; margin-bottom: 20px;">
                <thead>
                    <tr>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151;">Bot Name</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-align: center;">Type</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-align: center;">Leveraged USD</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-align: center;">Position</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-align: right;">Entry Px</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-align: right;">Current Px</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-align: center;">Trades</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-align: right;">Realized PnL</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-align: right;">Unrealized PnL</th>
                        <th style="background-color: #1f2937; color: #94a3b8; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-align: right;">Total PnL</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
            
            {losses_html}
        </div>
        
        <div class="footer" style="background-color: #0b0f19; padding: 20px; text-align: center; font-size: 11px; color: #64748b; border-top: 1px solid #1f2937;">
            <p style="margin: 0;">This statement is compiled and emailed automatically by your Coinbase Systematic Trading Bot platform daily at 9:00 AM.</p>
        </div>
    </div>
</body>
</html>"""

    # Send PnL Statement Email
    subject = f"📊 Daily PnL Statement: Live {total_live_pnl:+.2f} USD • Sim {total_sim_pnl:+.2f} USD"
    send_pnl_email(subject, html_content)

if __name__ == "__main__":
    main()
