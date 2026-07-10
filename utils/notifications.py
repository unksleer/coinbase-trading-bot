import os
import smtplib
from ssl import create_default_context
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import logging

logger = logging.getLogger("coinbase_bot")

def send_trade_notification(trade_type: str, pair: str, price: float, amount_usd: float, amount_base: float, fee_usd: float = 0.0, is_dry_run: bool = True, realized_pnl: float = 0.0):
    """
    Sends an email notification when a trade is executed by any of the bots.
    """
    smtp_enabled = os.getenv("EMAIL_NOTIFICATIONS_ENABLED", "False").lower() in ("true", "1", "yes")
    if not smtp_enabled:
        return
        
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    to_email = os.getenv("NOTIFICATION_EMAIL", smtp_user)
    
    if not smtp_host or not smtp_user or not smtp_pass:
        logger.warning("SMTP settings are incomplete. Cannot send trade notification.")
        return

    mode_str = "SIMULATED PRACTICE" if is_dry_run else "LIVE REAL-MONEY"
    emoji = "🚀 BUY" if trade_type == "BUY" else "⚠️ SELL"
    subject = f"{emoji} {pair} ({mode_str}) Trade Executed"
    
    # Custom color styling based on Buy/Sell
    header_bg = "linear-gradient(135deg, #10b981 0%, #059669 100%)" if trade_type == "BUY" else "linear-gradient(135deg, #ef4444 0%, #dc2626 100%)"
    
    pnl_row = ""
    if trade_type == "SELL":
        pnl_color = "#34d399" if realized_pnl >= 0 else "#f87171"
        pnl_row = f"""
        <tr style="background-color: #111827;">
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #9ca3af; font-weight: 500;">Realized PnL:</td>
            <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: {pnl_color}; font-weight: bold;">${realized_pnl:+.2f} USD</td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #0b0f19; color: #f1f5f9; margin: 0; padding: 20px;">
    <div class="container" style="max-width: 550px; margin: 0 auto; background-color: #111827; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4); border: 1px solid #1f2937;">
        <div class="header" style="background: {header_bg}; padding: 30px; text-align: center;">
            <h1 style="margin: 0; font-size: 22px; font-weight: 700; color: #ffffff; letter-spacing: 0.5px;">Trade Execution Alert</h1>
            <p style="margin: 5px 0 0 0; color: rgba(255, 255, 255, 0.8); font-size: 14px;">{mode_str} Mode</p>
        </div>
        <div class="content" style="padding: 25px;">
            <table style="width: 100%; border-collapse: collapse; font-size: 14px; text-align: left;">
                <tbody>
                    <tr style="background-color: #111827;">
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #9ca3af; font-weight: 500;">Trading Pair:</td>
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #ffffff; font-weight: bold;">{pair}</td>
                    </tr>
                    <tr style="background-color: #111827;">
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #9ca3af; font-weight: 500;">Action:</td>
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #ffffff; font-weight: bold;">
                            <span style="display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase; background-color: {'rgba(16, 185, 129, 0.2); color: #34d399;' if trade_type == 'BUY' else 'rgba(239, 68, 68, 0.2); color: #f87171;'}">{trade_type}</span>
                        </td>
                    </tr>
                    <tr style="background-color: #111827;">
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #9ca3af; font-weight: 500;">Execution Price:</td>
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #ffffff;">${price:,.4f} USD</td>
                    </tr>
                    <tr style="background-color: #111827;">
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #9ca3af; font-weight: 500;">Size ({pair.split('-')[0]}):</td>
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #ffffff;">{amount_base:.8f}</td>
                    </tr>
                    <tr style="background-color: #111827;">
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #9ca3af; font-weight: 500;">Total Cost/Value:</td>
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #ffffff;">${amount_usd:,.2f} USD</td>
                    </tr>
                    <tr style="background-color: #111827;">
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #9ca3af; font-weight: 500;">Execution Fee:</td>
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #ffffff;">${fee_usd:.4f} USD</td>
                    </tr>
                    {pnl_row}
                    <tr style="background-color: #111827;">
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #9ca3af; font-weight: 500;">Timestamp:</td>
                        <td style="padding: 12px 10px; border-bottom: 1px solid #1f2937; color: #ffffff;">{datetime.utcnow().isoformat()}Z</td>
                    </tr>
                </tbody>
            </table>
        </div>
        <div class="footer" style="background-color: #0b0f19; padding: 15px; text-align: center; font-size: 11px; color: #6b7280; border-top: 1px solid #1f2937;">
            <p style="margin: 0;">This alert was generated automatically by your Coinbase Trading Bot.</p>
        </div>
    </div>
</body>
</html>"""

    # Build MIME email message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Coinbase Bot <{smtp_user}>"
    msg["To"] = to_email
    
    part_html = MIMEText(html_content, "html")
    msg.attach(part_html)
    
    logger.info(f"Sending real-time trade email notification to {to_email}...")
    try:
        context = create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
        logger.info("Trade notification email sent successfully!")
    except Exception as e:
        logger.error(f"Error sending trade notification email: {e}")
