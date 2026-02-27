import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

DB_PATH = os.getenv("DB_PATH", "/home/phantomflip/PolyAlphaBot/data/polyalpha.db")  # adjust if needed

# These globals can be updated by your monitor if you import/set them
LAST_HEARTBEAT_TS = 0
LAST_ERROR = ""

def set_heartbeat():
    global LAST_HEARTBEAT_TS
    LAST_HEARTBEAT_TS = int(time.time())

def set_last_error(msg: str):
    global LAST_ERROR
    LAST_ERROR = (msg or "")[-1000:]  # keep last 1000 chars

def _db_scalar(query: str, params=()):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(query, params)
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0

def build_status_text() -> str:
    now = int(time.time())
    hb_age = (now - LAST_HEARTBEAT_TS) if LAST_HEARTBEAT_TS else None

    trades_15m = _db_scalar(
        "SELECT COUNT(*) FROM trades WHERE ts >= ?",
        (now - 15 * 60,)
    )
    open_positions = _db_scalar(
        "SELECT COUNT(*) FROM paper_positions WHERE status = 'OPEN'"
    )

    hb_line = "Heartbeat: never" if hb_age is None else f"Heartbeat age: {hb_age}s"
    err_line = "Last error: (none)" if not LAST_ERROR else f"Last error:\n{LAST_ERROR}"

    return (
        "🟢 PolyAlphaBot Status\n"
        f"- Time (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- {hb_line}\n"
        f"- Trades last 15m: {trades_15m}\n"
        f"- Open paper positions: {open_positions}\n\n"
        f"{err_line}"
    )

def build_report_text() -> str:
    now = int(time.time())
    # "today" in UTC; adjust to your preference if you want Vancouver local day boundaries
    start_of_day = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    trades_today = _db_scalar("SELECT COUNT(*) FROM trades WHERE ts >= ?", (start_of_day,))
    opened_today = _db_scalar(
        "SELECT COUNT(*) FROM paper_positions WHERE opened_ts >= ?",
        (start_of_day,)
    )

    last5 = []
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT ts, market_slug, side, price, size FROM trades ORDER BY ts DESC LIMIT 5")
    for r in cur.fetchall():
        ts, slug, side, price, size = r
        t = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")
        last5.append(f"{t} | {slug} | {side} | px={price} | sz={size}")
    conn.close()

    last5_text = "\n".join(last5) if last5 else "(none)"

    return (
        "📊 PolyAlphaBot Report (UTC)\n"
        f"- Trades today: {trades_today}\n"
        f"- Paper positions opened today: {opened_today}\n\n"
        "Last 5 trades:\n"
        f"{last5_text}"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_status_text())

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_report_text())

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong ✅")

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in env")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("report", cmd_report))

    app.run_polling(close_loop=False)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Telegram command server crashed:\n", traceback.format_exc())
        raise
