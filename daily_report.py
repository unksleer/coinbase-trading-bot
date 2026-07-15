import os
import sys
import time
import requests
import pandas as pd
import smtplib
from ssl import create_default_context
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Add root folder to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config.settings import Settings
from strategies import SMACrossoverStrategy, RSIMeanReversionStrategy

# List of top USD pairs on Coinbase to scan (identical to USDC movement)
COINS_TO_SCAN = [
    'BTC-USD', 'ETH-USD', 'SOL-USD', 'AVAX-USD', 'LINK-USD', 
    'ADA-USD', 'XRP-USD', 'DOGE-USD', 'NEAR-USD', 'SUI-USD', 
    'APT-USD', 'RENDER-USD', 'ICP-USD', 'DOT-USD', 'LTC-USD'
]

# Granularity mapping
GRANULARITY_MAP = {
    'ONE_MINUTE': 60,
    'FIVE_MINUTE': 300,
    'FIFTEEN_MINUTE': 900,
    'THIRTY_MINUTE': 1800,
    'ONE_HOUR': 3600,
    'TWO_HOUR': 7200,
    'SIX_HOUR': 21600,
    'ONE_DAY': 86400
}

def fetch_candles_public(product_id: str, granularity: str) -> pd.DataFrame:
    granularity_seconds = GRANULARITY_MAP.get(granularity, 3600)
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles"
    params = {
        "granularity": granularity_seconds
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data, columns=['start', 'low', 'high', 'open', 'close', 'volume'])
        df['start'] = pd.to_numeric(df['start'])
        df = df.sort_values(by='start', ascending=True).reset_index(drop=True)
        return df
    except:
        return pd.DataFrame()

def send_email(subject: str, html_content: str):
    # Retrieve SMTP settings
    smtp_enabled = os.getenv("EMAIL_NOTIFICATIONS_ENABLED", "False").lower() in ("true", "1", "yes")
    if not smtp_enabled:
        print("Email notifications are disabled. Skipping email send.")
        return
        
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    to_email = os.getenv("NOTIFICATION_EMAIL", smtp_user)
    
    if not smtp_host or not smtp_user or not smtp_pass:
        print("SMTP settings are incomplete. Skipping email send.")
        return
        
    # Build MIME email message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Coinbase Scanner <{smtp_user}>"
    msg["To"] = to_email
    
    part_html = MIMEText(html_content, "html")
    msg.attach(part_html)
    
    print(f"Sending email report to {to_email}...")
    try:
        context = create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
        print("Email sent successfully!")
    except Exception as e:
        print(f"Error sending email: {e}")

def main():
    print(f"[{datetime.now().isoformat()}] Starting Daily Market Scan Report...")
    
    # Initialize the strategy using current .env settings
    if Settings.STRATEGY_TYPE == "RSI_MEAN_REVERSION":
        strategy = RSIMeanReversionStrategy(
            rsi_period=Settings.RSI_PERIOD,
            rsi_oversold=Settings.RSI_OVERSOLD,
            rsi_overbought=Settings.RSI_OVERBOUGHT
        )
    else:
        strategy = SMACrossoverStrategy(
            short_window=Settings.SMA_SHORT_WINDOW,
            long_window=Settings.SMA_LONG_WINDOW,
            min_spread_percent=Settings.SMA_MIN_SPREAD_PERCENT,
            rsi_filter_enabled=Settings.RSI_FILTER_ENABLED,
            rsi_period=Settings.RSI_PERIOD,
            rsi_buy_min=Settings.RSI_BUY_MIN,
            rsi_buy_max=Settings.RSI_BUY_MAX,
            rsi_sell_min=Settings.RSI_SELL_MIN,
            ema_filter_enabled=Settings.EMA_TREND_FILTER_ENABLED,
            ema_period=Settings.EMA_TREND_PERIOD
        )
    required_candles = strategy.get_required_candles_count()
    
    results = []
    
    for coin in COINS_TO_SCAN:
        df = fetch_candles_public(coin, Settings.GRANULARITY)
        if df.empty or len(df) < required_candles:
            time.sleep(0.2)
            continue
            
        latest_price = float(df.iloc[-1]['close'])
        signal = strategy.evaluate(df)
        
        # Extract indicators
        spread = getattr(strategy, "last_spread_percent", 0.0)
        rsi = getattr(strategy, "last_rsi", 0.0)
        
        # Calculate 24h change
        gran_seconds = GRANULARITY_MAP.get(Settings.GRANULARITY, 3600)
        day_seconds = 86400
        candles_in_24h = int(day_seconds / gran_seconds)
        
        change_24h = 0.0
        if len(df) > candles_in_24h:
            past_price = float(df.iloc[-candles_in_24h]['close'])
            change_24h = ((latest_price - past_price) / past_price) * 100
            
        results.append({
            "Coin": coin.split('-')[0],
            "Price": latest_price,
            "24h Change %": change_24h,
            "RSI": rsi if rsi is not None else 0.0,
            "Spread %": spread if spread is not None else 0.0,
            "Signal": signal
        })
        time.sleep(0.2) # Rate limit protection
        
    if not results:
        print("No pairs successfully scanned. Aborting report.")
        return
        
    # Sort results: BUY signals first, then SELL signals, then Spread % descending
    scan_df = pd.DataFrame(results)
    def sort_key(row):
        sig_val = 2 if row['Signal'] == 'BUY' else (0 if row['Signal'] == 'SELL' else 1)
        return (sig_val, row['Spread %'], row['24h Change %'])
    scan_df['sort_val'] = scan_df.apply(sort_key, axis=1)
    scan_df = scan_df.sort_values(by='sort_val', ascending=False).drop(columns=['sort_val']).reset_index(drop=True)
    
    # Print detailed scan results to stdout (which logs to logs/daily_report.log)
    print("="*75)
    print(f"SCAN RESULTS - {datetime.now().isoformat()}Z")
    print("="*75)
    pd.options.display.float_format = '{:,.4f}'.format
    print(scan_df[["Coin", "Price", "24h Change %", "RSI", "Spread %", "Signal"]].to_string(index=False))
    print("="*75 + "\n")
    
    # Generate HTML Table
    table_rows_html = ""
    active_buys = []
    active_sells = []
    
    for _, row in scan_df.iterrows():
        coin = row["Coin"]
        price = f"${row['Price']:,.4f}" if row['Price'] < 10.0 else f"${row['Price']:,.2f}"
        change = f"{row['24h Change %']:+.2f}%"
        change_color = "#34d399" if row['24h Change %'] >= 0 else "#f87171"
        rsi = f"{row['RSI']:.1f}"
        spread = f"{row['Spread %']:.2f}%"
        
        if row["Signal"] == "BUY":
            badge_style = "background-color: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3);"
            active_buys.append(coin)
        elif row["Signal"] == "SELL":
            badge_style = "background-color: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3);"
            active_sells.append(coin)
        else:
            badge_style = "background-color: rgba(156, 163, 175, 0.2); color: #9ca3af; border: 1px solid rgba(156, 163, 175, 0.3);"
            
        table_rows_html += f"""
        <tr style="background-color: #111827;">
            <td style="font-weight: bold; color: #ffffff; padding: 12px 10px; border-bottom: 1px solid #1f2937;">{coin}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #cbd5e1;">{price}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: {change_color}; font-weight: 500;">{change}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #cbd5e1;">{rsi}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #cbd5e1;">{spread}</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937;"><span style="display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase; {badge_style}">{row['Signal']}</span></td>
        </tr>
        """
        
    # Generate Signal Highlights Alert HTML
    alert_html = ""
    if active_buys:
        alert_html += f"""
        <div class="signal-alert buys" style="border-radius: 8px; padding: 15px; margin-bottom: 25px; background-color: rgba(16, 185, 129, 0.1); border: 1px solid #10b981;">
            <div class="signal-title" style="font-weight: bold; font-size: 16px; margin-bottom: 10px; color: #34d399;">🚀 BUY SIGNAL ALERT ({len(active_buys)})</div>
            <ul class="signal-list" style="margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.5; color: #e2e8f0;">
                {"".join([f"<li><b>{c}</b> is triggering a BUY signal. Market momentum is turning positive.</li>" for c in active_buys])}
            </ul>
        </div>
        """
    if active_sells:
        alert_html += f"""
        <div class="signal-alert sells" style="border-radius: 8px; padding: 15px; margin-bottom: 25px; background-color: rgba(239, 68, 68, 0.1); border: 1px solid #ef4444;">
            <div class="signal-title" style="font-weight: bold; font-size: 16px; margin-bottom: 10px; color: #f87171;">⚠️ SELL SIGNAL ALERT ({len(active_sells)})</div>
            <ul class="signal-list" style="margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.5; color: #e2e8f0;">
                {"".join([f"<li><b>{c}</b> is triggering a SELL signal. Consider exiting/taking profits.</li>" for c in active_sells])}
            </ul>
        </div>
        """
        
    if not active_buys and not active_sells:
        alert_html += """
        <div class="signal-alert none" style="border-radius: 8px; padding: 15px; margin-bottom: 25px; background-color: rgba(156, 163, 175, 0.1); border: 1px solid #6b7280;">
            <div class="signal-title" style="font-weight: bold; font-size: 16px; margin-bottom: 10px; color: #9ca3af;">ℹ️ Market Neutral</div>
            <p class="signal-list" style="margin: 0; padding: 0; list-style: none; font-size: 14px; line-height: 1.5; color: #e2e8f0;">No active BUY or SELL crossover signals were detected. All assets are currently in HOLD state.</p>
        </div>
        """

    # Build the full HTML Document
    date_str = datetime.now().strftime("%B %d, %Y")
    html_template = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #0b0f19; color: #f1f5f9; margin: 0; padding: 20px;">
    <div class="container" style="max-width: 650px; margin: 0 auto; background-color: #111827; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4); border: 1px solid #1f2937;">
        <div class="header" style="background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%); padding: 35px 30px; text-align: center;">
            <h1 style="margin: 0; font-size: 24px; font-weight: 700; color: #ffffff; letter-spacing: 0.5px;">Crypto Crossover Screener</h1>
            <p style="margin: 5px 0 0 0; color: #93c5fd; font-size: 14px;">Daily Momentum Scanner Report • {date_str}</p>
        </div>
        <div class="content" style="padding: 30px;">
            {alert_html}
            
            <h3 style="margin-top: 0; color: #ffffff; font-size: 16px; font-weight: 600;">Market Scanning Report ({Settings.GRANULARITY})</h3>
            <p style="font-size: 13px; color: #9ca3af; margin-top: -10px; margin-bottom: 20px;">Scanned using SMA crossover windows ({Settings.SMA_SHORT_WINDOW}/{Settings.SMA_LONG_WINDOW}) and filters.</p>
            
            <table style="width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 14px; text-align: left;">
                <thead>
                    <tr>
                        <th style="background-color: #1f2937; color: #9ca3af; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px;">Asset</th>
                        <th style="background-color: #1f2937; color: #9ca3af; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px;">Price</th>
                        <th style="background-color: #1f2937; color: #9ca3af; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px;">24h Chg</th>
                        <th style="background-color: #1f2937; color: #9ca3af; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px;">RSI</th>
                        <th style="background-color: #1f2937; color: #9ca3af; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px;">Spread</th>
                        <th style="background-color: #1f2937; color: #9ca3af; font-weight: 600; padding: 12px 10px; border-bottom: 2px solid #374151; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px;">Signal</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows_html}
                </tbody>
            </table>
        </div>
        <div class="footer" style="background-color: #0b0f19; padding: 20px; text-align: center; font-size: 11px; color: #6b7280; border-top: 1px solid #1f2937;">
            <p style="margin: 0;">This report is generated automatically by your Coinbase Trading Bot screener system.</p>
        </div>
    </div>
</body>
</html>"""

    # Send the email only if there are active BUY signals (Alert-Only Mode)
    if active_buys:
        subject = f"🚀 BUY SIGNAL: {', '.join(active_buys)} • Crypto Report"
        send_email(subject, html_template)
    else:
        print(f"[{datetime.now().isoformat()}] No active BUY signals. Skipping email to avoid spam.")

if __name__ == "__main__":
    main()
