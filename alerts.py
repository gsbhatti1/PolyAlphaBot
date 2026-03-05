import os, asyncio, logging, time
import httpx

logger = logging.getLogger("polymarket-bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK    = os.getenv("DISCORD_WEBHOOK_URL", "")

def _tg_url():
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

def _send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        with httpx.Client(timeout=8) as c:
            c.post(_tg_url(), json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML"
            })
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")

def send_trade_opened(market_slug, market_question, side, outcome, size_usd, entry_price, bankroll, kelly_fraction):
    q = (market_question or market_slug or "")[:55]
    emoji = "🟢"
    ts = time.strftime("%H:%M UTC", time.gmtime())
    text = (
        f"{emoji} <b>TRADE OPENED</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{q}</b>\n"
        f"🎯 Side: <b>{side} {outcome}</b>\n"
        f"💵 Size: <b>${size_usd:.2f}</b>\n"
        f"📈 Entry: <b>${entry_price:.4f}</b>\n"
        f"⚡ Kelly: <b>{kelly_fraction*100:.0f}%</b>\n"
        f"🏦 Bankroll: <b>${bankroll:.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {ts}"
    )
    _send(text)

def send_trade_closed(market_slug, market_question, side, outcome, size_usd, entry_price, exit_price, pnl, bankroll):
    q = (market_question or market_slug or "")[:55]
    if pnl > 0:
        emoji = "✅"
        result = f"+${pnl:.2f} WIN"
    elif pnl < 0:
        emoji = "❌"
        result = f"-${abs(pnl):.2f} LOSS"
    else:
        emoji = "➖"
        result = "$0.00 FLAT"
    ts = time.strftime("%H:%M UTC", time.gmtime())
    text = (
        f"🔴 <b>TRADE CLOSED</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{q}</b>\n"
        f"🎯 Side: <b>{side} {outcome}</b>\n"
        f"💵 Size: <b>${size_usd:.2f}</b>\n"
        f"📊 PnL: <b>{result}</b> {emoji}\n"
        f"📈 Entry: ${entry_price:.4f} → Exit: ${exit_price:.4f}\n"
        f"🏦 Bankroll: <b>${bankroll:.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {ts}"
    )
    _send(text)

def send_hourly_report(bankroll, starting_bankroll, pnl_today, open_count, locked_usd, wins, losses, open_positions):
    win_rate = (wins/(wins+losses)*100) if (wins+losses) > 0 else 0
    pnl_pct = (pnl_today/starting_bankroll*100) if starting_bankroll > 0 else 0
    pnl_sign = "+" if pnl_today >= 0 else ""
    ts = time.strftime("%H:%M UTC", time.gmtime())
    
    pos_lines = ""
    for p in (open_positions or [])[:5]:
        slug = str(p.get("market_slug",""))[:30]
        side = p.get("side","")
        sz   = p.get("size_usd", 0)
        ep   = p.get("entry_price", 0)
        pos_lines += f"  • {slug} | {side} ${sz:.0f} @ ${ep:.3f}\n"
    
    text = (
        f"📊 <b>HOURLY REPORT</b> — {ts}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏦 Bankroll:    <b>${bankroll:.2f}</b>\n"
        f"📈 PnL:         <b>{pnl_sign}${pnl_today:.2f} ({pnl_sign}{pnl_pct:.2f}%)</b>\n"
        f"📦 Open:        <b>{open_count}</b> positions (${locked_usd:.2f} locked)\n"
        f"✅ Wins: <b>{wins}</b>  ❌ Losses: <b>{losses}</b>\n"
        f"🎯 Win Rate:    <b>{win_rate:.1f}%</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
    )
    if pos_lines:
        text += f"<b>Open positions:</b>\n{pos_lines}"
    _send(text)

def send_startup(bankroll):
    ts = time.strftime("%H:%M UTC", time.gmtime())
    text = (
        f"🚀 <b>PolyAlphaBot STARTED</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏦 Bankroll: <b>${bankroll:.2f}</b>\n"
        f"⚙️ Mode: <b>PAPER</b>\n"
        f"💵 Max per trade: <b>$10</b>\n"
        f"🔄 Cooldown: <b>3 min</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {ts}"
    )
    _send(text)

def send_alert(msg: str):
    _send(f"⚠️ <b>ALERT</b>\n{msg}")

# Legacy compatibility
def send_telegram(msg: str):
    _send(msg)

async def send_telegram_async(msg: str):
    _send(msg)

async def send_discord(msg: str):
    if not DISCORD_WEBHOOK:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            await c.post(DISCORD_WEBHOOK, json={"content": msg})
    except Exception as e:
        logger.warning(f"Discord send failed: {e}")

# ── Telegram getUpdates (command polling) ─────────────────────────────────
def get_telegram_updates_sync(offset: int = 0, timeout_sec: int = 15) -> dict:
    """
    Poll Telegram Bot API for new updates (used by command loop).
    Returns the raw JSON response dict, or {} on error.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {}
    try:
        import httpx
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        r = httpx.get(url, params={"offset": offset, "timeout": timeout_sec}, timeout=timeout_sec + 5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


# ── Legacy sync wrapper (monitor calls this) ──────────────────────────────
def send_telegram_sync(msg: str):
    """Direct wrapper — monitor uses this for all alerts."""
    _send(msg)

# ── Trade alert formatter (monitor calls this on new trade) ──────────────
def format_new_trade_alert(wallet: dict, trade: dict, paper_action: dict) -> str:
    market_q = str(trade.get("market_question") or trade.get("market_slug") or "")[:55]
    side      = str(trade.get("side",""))
    outcome   = str(trade.get("outcome",""))
    size_usd  = float((paper_action or {}).get("size_usd") or trade.get("size_usd") or 0)
    entry     = float((paper_action or {}).get("entry_price") or trade.get("price") or 0)
    bankroll  = float((paper_action or {}).get("bankroll") or 0)
    kelly     = float((paper_action or {}).get("kelly_fraction") or 0)
    wallet_u  = str(wallet.get("username") or wallet.get("address",""))[:20]
    ts        = time.strftime("%H:%M UTC", time.gmtime())
    return (
        f"🟢 <b>TRADE OPENED</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{market_q}</b>\n"
        f"🎯 Side: <b>{side} {outcome}</b>\n"
        f"💵 Size: <b>${size_usd:.2f}</b>\n"
        f"📈 Entry: <b>${entry:.4f}</b>\n"
        f"⚡ Kelly: <b>{kelly*100:.0f}%</b>\n"
        f"👛 Copying: <b>{wallet_u}</b>\n"
        f"🏦 Bankroll: <b>${bankroll:.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {ts}"
    )

# ── Close alert (call this when position closes) ─────────────────────────
def format_close_alert(pos: dict, bankroll: float) -> str:
    market_q  = str(pos.get("market_question") or pos.get("market_slug") or "")[:55]
    side      = str(pos.get("side",""))
    outcome   = str(pos.get("outcome",""))
    size_usd  = float(pos.get("size_usd") or 0)
    entry     = float(pos.get("entry_price") or 0)
    exit_p    = float(pos.get("exit_price") or 0)
    pnl       = float(pos.get("pnl") or 0)
    ts        = time.strftime("%H:%M UTC", time.gmtime())
    if pnl > 0:
        result = f"+${pnl:.2f} ✅ WIN"
    elif pnl < 0:
        result = f"-${abs(pnl):.2f} ❌ LOSS"
    else:
        result  = "$0.00 ➖ FLAT"
    return (
        f"🔴 <b>TRADE CLOSED</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{market_q}</b>\n"
        f"🎯 Side: <b>{side} {outcome}</b>\n"
        f"💵 Size: <b>${size_usd:.2f}</b>  |  PnL: <b>{result}</b>\n"
        f"📈 Entry: ${entry:.4f} → Exit: ${exit_p:.4f}\n"
        f"🏦 Bankroll: <b>${bankroll:.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {ts}"
    )
