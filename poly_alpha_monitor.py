#!/usr/bin/env python3
"""
Polymarket Hidden Alpha Monitor.

Watches discovered wallets for new trades and paper-trades alongside them.

Usage:
    python poly_alpha_monitor.py [OPTIONS]

Options:
    --max-wallets   Max wallets to watch (default: 20)
    --interval      Poll interval in seconds (default: 30)
    --min-size      Min trade size to alert on (default: $50)
    --wallet        Add a specific wallet address to watch
    --bankroll      Starting paper bankroll (default: $10,000)
    --no-paper      Disable paper trading
    --db            SQLite DB path (default: poly_alpha.db)
"""
import asyncio
import json
import signal
import sys
import os
import time
import logging
from pathlib import Path
from datetime import datetime

import click
import httpx
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel

import config
import config
print(f"[BOOT] config file: {config.__file__}")
print(f"[BOOT] POLL_INTERVAL={config.POLL_INTERVAL} MAX_WATCH_WALLETS={config.MAX_WATCH_WALLETS} MIN_TRADE_SIZE={config.MIN_TRADE_SIZE}")

import db
import poly_api
import alerts
from paper_trader import PaperTrader

# ── Autonomous maintenance defaults (override in config.py if you want)
DEAD_WALLET_DAYS = getattr(config, 'DEAD_WALLET_DAYS', 5)
MAINTENANCE_EVERY_SEC = getattr(config, 'MAINTENANCE_EVERY_SEC', 3600)
SCAN_REFRESH_EVERY_SEC = getattr(config, 'SCAN_REFRESH_EVERY_SEC', 21600)
SCAN_LIMIT = getattr(config, 'SCAN_LIMIT', 1000)
SCANNER_SCRIPT = getattr(config, 'SCANNER_SCRIPT', 'poly_alpha_scanner.py')

REFILL_FROM_INACTIVE = getattr(config, 'REFILL_FROM_INACTIVE', True)


def telegram_portfolio_snapshot(conn, paper):
    """Send a visual portfolio snapshot to Telegram."""
    try:
        s = paper.get_summary()
        last_n = int(getattr(config, "TELEGRAM_PORTFOLIO_LAST_N", 5))

        # open exposure + open positions (already in summary)
        open_positions = s.get("open_positions", 0)
        open_exposure = s.get("open_exposure", 0)

        # last opens
        last_open = conn.execute(
            """
            SELECT id, market_slug, outcome, side, entry_price, size_usd, opened_at
            FROM paper_positions
            ORDER BY id DESC
            LIMIT ?
            """,
            (last_n,),
        ).fetchall()

        # last closes (if you have closed_at column populated)
        last_close = conn.execute(
            """
            SELECT id, market_slug, outcome, side, entry_price, exit_price, size_usd, pnl, closed_at
            FROM paper_positions
            WHERE closed_at IS NOT NULL AND closed_at != 0
            ORDER BY closed_at DESC
            LIMIT ?
            """,
            (last_n,),
        ).fetchall()

        def fmt_row(r):
            slug = (r["market_slug"] or "")[:28]
            return f"- {slug} | {r.get('outcome','')}/{r.get('side','')} | ${float(r.get('size_usd',0)):,.0f}"

        lines = []
        lines.append("📊 *Paper Portfolio Snapshot*")
        lines.append(f"Bankroll: *${s['bankroll']:,.2f}*")
        lines.append(f"P&L: *${s['total_pnl']:+,.2f}*   Return: *{s['total_return_pct']:+.2f}%*")
        lines.append(f"Trades: {s['total_trades']} (W:{s['wins']} / L:{s['losses']})   Win%: {s['win_rate']:.1%}")
        lines.append(f"Open: {open_positions}   Exposure: *${float(open_exposure):,.0f}*")
        if last_open:
            lines.append("")
            lines.append("🟦 *Last Opens*")
            for r in last_open:
                lines.append(fmt_row(r))
        if last_close:
            lines.append("")
            lines.append("🟥 *Last Closes*")
            for r in last_close:
                slug = (r["market_slug"] or "")[:28]
                lines.append(f"- {slug} | {r.get('outcome','')}/{r.get('side','')} | ${float(r.get('size_usd',0)):,.0f} | PnL ${float(r.get('pnl',0)):+,.2f}")

        alerts.send_telegram_sync("\n".join(lines))
    except Exception as e:
        logger.warning("[telegram] portfolio snapshot failed: %r", e)

def telegram_heartbeat(text: str) -> None:
    try:
        print("[telegram] heartbeat: sync", file=sys.stderr)
        alerts.send_telegram_sync(text)
    except Exception as e:
        print(f"[telegram] heartbeat failed: {e!r}", file=sys.stderr)

console = Console()
logger = logging.getLogger('polymarket-bot')
logging.basicConfig(level=logging.INFO)

# Quiet noisy httpx INFO logs
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
RUNNING = True
TELEGRAM_UPDATE_OFFSET = None
LAST_REPORT_STATE = None
LAST_REPORT_SENT_TS = 0
LAST_PAPER_TS = {}
CONSENSUS_TRACKER = {}
LAST_PORTFOLIO_TG_TS = 0
LAST_DECISION_LOG_TS = 0
DECISION_COUNTS = {
  'detected': 0,
  'min_size_skip': 0,
  'db_dup_trade': 0,
  'kelly_skip': 0,
  'cooldown_skip': 0,
  'cap_or_dup_skip': 0,
  'opened': 0,
}


def handle_signal(sig, frame):
    global RUNNING
    RUNNING = False
    console.print("\n[yellow]Shutting down gracefully...[/]")


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def log_decisions_summary():
    try:
        logger.info(
            "[DECISIONS] detected=%d opened=%d kelly_skip=%d cap/dup=%d cooldown=%d min_size=%d db_dup=%d",
            DECISION_COUNTS.get('detected',0),
            DECISION_COUNTS.get('opened',0),
            DECISION_COUNTS.get('kelly_skip',0),
            DECISION_COUNTS.get('cap_or_dup_skip',0),
            DECISION_COUNTS.get('cooldown_skip',0),
            DECISION_COUNTS.get('min_size_skip',0),
            DECISION_COUNTS.get('db_dup_trade',0),
        )
    except Exception as _poll_err:
        pass



def build_portfolio_report(conn, paper) -> str:
    """Visual Telegram portfolio snapshot (Markdown)."""
    s = paper.get_summary()

    bankroll      = float(s.get("bankroll", 0))
    starting      = float(s.get("starting_bankroll", 1000))
    pnl           = float(s.get("total_pnl", 0))
    exposure      = float(s.get("open_exposure", 0))   # correct key
    open_pos      = int(s.get("open_positions", 0))
    total_trades  = int(s.get("total_trades", 0))
    wins          = int(s.get("wins", 0))
    losses        = int(s.get("losses", 0))
    win_rate      = float(s.get("win_rate", 0)) * 100
    total_return  = round(((bankroll + exposure - starting) / starting) * 100, 2)

    pnl_emoji  = "🟢" if pnl >= 0 else "🔴"
    ret_emoji  = "📈" if total_return >= 0 else "📉"

    # ── Open positions detail ────────────────────────────────────────────
    import sqlite3 as _sq
    open_rows = conn.execute(
        """
        SELECT market_slug, side, outcome, entry_price, size_usd, opened_at
        FROM paper_positions WHERE status='open'
        ORDER BY opened_at DESC
        """
    ).fetchall()

    # ── Last 10 closed trades ─────────────────────────────────────────────
    closed_rows = conn.execute(
        """
        SELECT market_slug, side, outcome, entry_price, exit_price, pnl, closed_at
        FROM paper_positions WHERE status='closed'
        ORDER BY closed_at DESC LIMIT 10
        """
    ).fetchall()

    # ── Last 8 outbox actions ─────────────────────────────────────────────
    outbox_rows = conn.execute(
        """
        SELECT ts, market_slug, side, status, error
        FROM order_outbox ORDER BY ts DESC LIMIT 8
        """
    ).fetchall()

    import datetime as _dt

    def _ts(t):
        try:
            return _dt.datetime.fromtimestamp(float(t)).strftime('%H:%M')
        except Exception as _poll_err:
            return '??:??'

    lines = [
        "```",
        "=" * 49,
        "  POLY ALPHA BOT — REPORT",
        "=" * 49,
        f"  Bankroll:      ${bankroll:,.2f}   (started ${starting:,.0f})",
        f"  Open:          {open_pos} positions  (${exposure:.2f} locked)",
        f"  Realised PnL:  ${pnl:+,.2f}",
        f"  Closed trades: {total_trades}  (W:{wins} L:{losses}  WR:{win_rate:.1f}%)",
        f"  Total Return:  {total_return:+.2f}%",
    ]

    lines += ["", "  OPEN POSITIONS", "  " + "-" * 47]
    if open_rows:
        for r in open_rows:
            slug = (r['market_slug'] or '')[:22]
            out  = str(r['outcome'] or '')[:8]
            lines.append(
                f"  {_ts(r['opened_at'])} {slug:<22} {r['side']:<4} {out:<8} "
                f"e={float(r['entry_price']):.3f} ${float(r['size_usd']):.0f}"
            )
    else:
        lines.append("  No open positions")

    lines += ["", "  LAST 10 CLOSED", "  " + "-" * 47]
    if closed_rows:
        for r in closed_rows:
            slug = (r['market_slug'] or '')[:22]
            pnl_s = f"${float(r['pnl']):+.2f}"
            lines.append(
                f"  {_ts(r['closed_at'])} {slug:<22} "
                f"e={float(r['entry_price']):.3f}→{float(r['exit_price']):.3f} {pnl_s}"
            )
    else:
        lines.append("  No closed trades yet")

    lines += ["", "  LAST 8 ACTIONS", "  " + "-" * 47]
    for r in outbox_rows:
        err  = str(r['error'] or '')[:28]
        lines.append(f"  {_ts(r['ts'])} {r['status']:<8} {(r['market_slug'] or '')[:20]:<20} {r['side']:<4} {err}")

    lines += ["=" * 49, "```"]
    return "\n".join(lines)

def should_send_report(prev: dict | None, cur: dict) -> bool:
    if prev is None:
        return True
    if abs(cur["bankroll"] - prev["bankroll"]) >= float(getattr(config, "REPORT_DELTA_BANKROLL", 25)):
        return True
    if abs(cur["pnl"] - prev["pnl"]) >= float(getattr(config, "REPORT_DELTA_PNL", 10)):
        return True
    if abs(cur["open_exposure"] - prev["open_exposure"]) >= float(getattr(config, "REPORT_DELTA_EXPOSURE", 50)):
        return True
    if cur["open_positions"] != prev["open_positions"] and abs(cur["open_positions"] - prev["open_positions"]) >= int(getattr(config, "REPORT_DELTA_OPEN_POS", 1)):
        return True
    return False


async def wallet_rescan_loop(conn):
    """
    Weekly wallet quality monitor.
    Drops wallets whose last-30-day win rate fell below 55% — stale edge.
    Logs how many wallets were pruned each cycle.
    """
    rescan_interval = int(getattr(config, "WALLET_RESCAN_SEC", 7 * 24 * 3600))  # 7 days default
    min_recent_wr   = float(getattr(config, "WALLET_MIN_RECENT_WIN_RATE", 0.55))
    min_recent_days = int(getattr(config, "WALLET_MIN_RECENT_DAYS", 30))

    while RUNNING:
        try:
            await asyncio.sleep(rescan_interval)
            if not RUNNING:
                break

            logger.info("[RESCAN] Starting weekly wallet quality check...")
            cutoff_ts = time.time() - (min_recent_days * 86400)

            # Find wallets that haven't traded recently or have low recent win rate
            rows = conn.execute(
                "SELECT address, username, win_rate, last_trade_ts, pnl FROM wallets WHERE is_active=1"
            ).fetchall()

            pruned = 0
            kept   = 0
            for r in rows:
                last_ts  = float(r["last_trade_ts"] or 0)
                win_rate = float(r["win_rate"] or 0)
                pnl      = float(r["pnl"] or 0)

                # Drop if inactive for 30+ days AND win rate is poor
                inactive = last_ts < cutoff_ts
                poor_wr  = win_rate < min_recent_wr
                tiny_pnl = pnl < 50_000

                if inactive and poor_wr and tiny_pnl:
                    conn.execute(
                        "UPDATE wallets SET is_active=0 WHERE address=?",
                        (r["address"],)
                    )
                    pruned += 1
                else:
                    kept += 1

            conn.commit()
            logger.info("[RESCAN] Done: kept=%d pruned=%d (win_rate<%.0f%% + inactive + pnl<$50k)",
                        kept, pruned, min_recent_wr * 100)

            if pruned > 0:
                msg = (f"🔄 *Weekly Wallet Rescan*\n"
                       f"Pruned {pruned} underperforming wallets\n"
                       f"Active wallets: {kept}")
                alerts.send_telegram_sync(msg)

        except Exception as e:
            logger.warning("[RESCAN] error: %r", e)
            await asyncio.sleep(3600)


async def telegram_command_loop(conn, paper):
    """Poll Telegram for /status and /report and reply."""
    global TELEGRAM_UPDATE_OFFSET
    poll_sec = int(getattr(config, "TELEGRAM_CMD_POLL_SEC", 5))

    while RUNNING:
        try:
            data = alerts.get_telegram_updates_sync(offset=TELEGRAM_UPDATE_OFFSET, timeout_sec=15) or {}
            if data.get("ok"):
                for u in (data.get("result") or []):
                    TELEGRAM_UPDATE_OFFSET = int(u.get("update_id", 0)) + 1
                    msg = u.get("message") or {}
                    chat = msg.get("chat") or {}
                    chat_id = str(chat.get("id", ""))

                    # Only answer your configured chat
                    if str(getattr(config, "TELEGRAM_CHAT_ID", "")) and str(getattr(config, "TELEGRAM_CHAT_ID")) != chat_id:
                        continue

                    text = (msg.get("text") or "").strip().lower()
                    if text in ("/status", "/report"):
                        alerts.send_telegram_sync(build_portfolio_report(conn, paper))
                    elif text == "/positions":
                        rows = conn.execute(
                            """
                            SELECT market_slug, outcome, side, size_usd
                            FROM paper_positions
                            WHERE closed_at IS NULL OR closed_at=0
                            ORDER BY id DESC
                            LIMIT 15
                            """
                        ).fetchall()
                        out = ["📦 *Open Positions*"]
                        if not rows:
                            out.append("— none —")
                        else:
                            for r in rows:
                                slug = (r["market_slug"] or "")[:28]
                                out.append(f"- `{slug}` | {r.get('side','?')} {r.get('outcome','?')} | ${float(r.get('size_usd',0)):,.0f}")
                        alerts.send_telegram_sync("\n".join(out))
        except Exception as e:
            logger.warning("[telegram] command loop error: %r", e)

        await asyncio.sleep(poll_sec)




# ── Telegram Alert Rate Limiter ─────────────────────────────
ALERT_WINDOW_START = 0
ALERT_COUNT = 0

def can_send_alert():
    global ALERT_WINDOW_START, ALERT_COUNT

    max_per_min = int(getattr(config, "MAX_TELEGRAM_ALERTS_PER_MIN", 12))
    now = time.time()

    if ALERT_WINDOW_START == 0 or (now - ALERT_WINDOW_START) >= 60:
        ALERT_WINDOW_START = now
        ALERT_COUNT = 0

    if ALERT_COUNT < max_per_min:
        ALERT_COUNT += 1
        return True

    return False

def build_dashboard(
    wallets: list[dict],
    recent_trades: list[dict],
    paper: PaperTrader | None,
    poll_count: int,
    last_poll: float,
) -> Panel:
    """Build the live dashboard display."""
    # ── Wallet table
    w_table = Table(
        title="Watched Wallets",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    w_table.add_column("Name", min_width=14)
    w_table.add_column("Alpha", justify="right", width=7)
    w_table.add_column("PnL", justify="right", width=10)
    w_table.add_column("Win%", justify="right", width=7)
    w_table.add_column("Last Trade", justify="right", width=12)

    for w in wallets[:15]:
        last = w.get("last_updated", 0)
        last_str = (
            datetime.fromtimestamp(last).strftime("%m/%d %H:%M")
            if last else "—"
        )
        w_table.add_row(
            (w.get("username") or w["address"][:10]),
            f"{w.get('alpha_score', 0):.3f}",
            f"${w.get('pnl', 0):,.0f}",
            f"{w.get('win_rate', 0):.1%}",
            last_str,
        )

    # ── Recent trades table
    t_table = Table(
        title="Recent Detections",
        show_header=True,
        header_style="bold green",
        expand=True,
    )
    t_table.add_column("Time", width=8)
    t_table.add_column("Wallet", min_width=10)
    t_table.add_column("Market", min_width=20)
    t_table.add_column("Side", width=8)
    t_table.add_column("Size", justify="right", width=10)
    t_table.add_column("Paper", justify="right", width=10)

    for t in recent_trades[-10:]:
        ts = datetime.fromtimestamp(t.get("detected_at", time.time()))
        t_table.add_row(
            ts.strftime("%H:%M:%S"),
            t.get("wallet_name", "?")[:12],
            (t.get("market_question", "?"))[:30],
            t.get("side", "?"),
            f"${float(t.get('size_usd', 0)):,.0f}",
            f"${t.get('paper_size', 0):,.0f}" if t.get("paper_size") else "—",
        )

    # ── Paper trading summary
    paper_text = ""
    if paper:
        s = paper.get_summary()
        pnl_color = "green" if s["total_pnl"] >= 0 else "red"
        ret_color = "green" if s["total_return_pct"] >= 0 else "red"
        paper_text = (
            f"[bold]Paper Portfolio[/]  "
            f"Bankroll: ${s['bankroll']:,.2f}  "
            f"Return: [{ret_color}]{s['total_return_pct']:+.2f}%[/]  "
            f"P&L: [{pnl_color}]${s['total_pnl']:+,.2f}[/]  "
            f"Open: {s['open_positions']}  "
            f"Closed: {s['total_trades']}  "
            f"Win rate: {s['win_rate']:.1%}"
        )

    ago = time.time() - last_poll if last_poll else 0
    status = (
        f"Polls: {poll_count} · Last: {ago:.0f}s ago · "
        f"Watching: {len(wallets)} wallets"
    )

    content = f"{w_table}\n\n{t_table}"
    if paper_text:
        content += f"\n\n{paper_text}"
    content += f"\n\n[dim]{status}[/]"

    return Panel(
        content,
        title="[bold cyan]Polymarket Alpha Monitor[/]",
        border_style="cyan",
    )


def report_state(conn, paper):
    """Build a dict of current portfolio state for change-driven reporting."""
    s = paper.get_summary()
    return {
        "bankroll": float(s.get("bankroll", 0)),
        "pnl": float(s.get("total_pnl", 0)),
        "open_positions": int(s.get("open_positions", 0)),
        "open_exposure": float(s.get("open_exposure", 0)),
    }


async def run_maintenance(conn, max_wallets):
    """Prune dead wallets and refill from inactive pool."""
    pruned = db.prune_dead_wallets(conn, DEAD_WALLET_DAYS)
    active = db.get_active_wallets(conn, limit=max_wallets)
    slots = max_wallets - len(active)
    added = []
    if slots > 0 and REFILL_FROM_INACTIVE:
        added = db.activate_best_inactive(conn, slots)
    active_n = len(db.get_active_wallets(conn, limit=max_wallets))
    return pruned, added, active_n


async def poll_wallet(
    client: httpx.AsyncClient,
    conn,
    wallet: dict,
    paper: PaperTrader | None,
    min_size: float,
) -> list[dict]:
    """
    Check a wallet for new trades since last check.
    Returns list of new trade dicts.
    """
    addr = wallet["address"]
    last_ts = db.get_latest_trade_ts(conn, addr)

    # Use activity endpoint: GET /activity?user=<addr>
    activity = await poly_api.fetch_activity(client, addr, limit=20)
    if not activity:
        return []

    new_trades = []
    for trade in activity:
        # Only process actual trades
        if trade.get("type") and trade["type"] != "TRADE":
            continue

        ts = trade.get("timestamp", 0)
        try:
            ts = float(ts)
            if ts > 1e12:
                ts /= 1000
        except (ValueError, TypeError):
            continue

        if ts <= last_ts:
            continue

        # Activity fields: usdcSize (USD), size (tokens), price, side, etc.
        size = float(trade.get("usdcSize", trade.get("size", 0)) or 0)
        if size < 0:  # disabled — we size ourselves via Kelly
            DECISION_COUNTS['min_size_skip'] += 1
            continue

        tx_hash = trade.get("transactionHash", f"{addr}_{ts}")

        trade_record = {
            "wallet_address": addr,
            "wallet_name": wallet.get("username", addr[:10]),
            "market_slug": trade.get("slug", trade.get("eventSlug", "")),
            "market_question": trade.get("title", ""),
            "outcome": trade.get("outcome", ""),
            "side": trade.get("side", ""),
            "size_usd": size,
            "price": float(trade.get("price", 0) or 0),
            "timestamp": ts,
            "tx_hash": tx_hash,
            "detected_at": time.time(),
        }

        # Insert into DB (dedup by tx_hash)
        is_new = db.insert_trade(conn, trade_record)
        if not is_new:
            DECISION_COUNTS['db_dup_trade'] += 1
            continue

        DECISION_COUNTS['detected'] += 1


        # Save features snapshot for learning (dedup by tx_hash)
        try:
            wallet_snapshot = {
                "alpha_score": float(wallet.get("alpha_score") or 0),
                "win_rate": float(wallet.get("win_rate") or 0),
                "profit_factor": float(wallet.get("profit_factor") or 0),
                "sharpe_ratio": float(wallet.get("sharpe_ratio") or 0),
                "consistency": float(wallet.get("consistency") or 0),
                "recency_score": float(wallet.get("recency_score") or 0),
                "visibility": float(wallet.get("visibility") or 0),
                "markets_traded": float(wallet.get("markets_traded") or 0),
                "avg_bet_size": float(wallet.get("avg_bet_size") or 0),
                "wallet_keys": list(wallet.keys()),
            }

            conn.execute(
                """
                INSERT OR IGNORE INTO trade_features (
                  tx_hash, detected_at, wallet_address, market_slug, outcome, side,
                  size_usd, price, timestamp, features
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                  trade_record.get("tx_hash"),
                  trade_record.get("detected_at"),
                  addr,
                  trade_record.get("market_slug"),
                  trade_record.get("outcome"),
                  trade_record.get("side"),
                  float(trade_record.get("size_usd") or 0),
                  float(trade_record.get("price") or 0),
                  float(trade_record.get("timestamp") or 0),
                  json.dumps(wallet_snapshot),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.warning("[learn] trade_features insert failed: %r", e)


        # Paper trade
        paper_size = 0
        if paper:
            # Pass full wallet so size_trade uses real win_rate + profit_factor
            wallet_metrics = {
                "alpha":         wallet.get("alpha_score", wallet.get("alpha", 0.5)),
                "win_rate":      wallet.get("win_rate", 0.55),
                "profit_factor": wallet.get("profit_factor", 1.2),
                "pnl":           wallet.get("pnl", 0),
            }
            # Ensure price is in trade dict for real Kelly calculation
            if "price" not in trade and "price" in trade_record:
                trade["price"] = trade_record["price"]
            sizing = paper.size_trade(wallet_metrics, trade)
            logger.info("[PAPER_DEBUG] sizing=%s wallet_keys=%s", sizing, list(wallet.keys()))
            if sizing:
                cd = int(getattr(config, 'WALLET_COOLDOWN_SEC', 0))
                now = time.time()
                last = LAST_PAPER_TS.get(addr, 0)
                if (now - last) < cd:
                    DECISION_COUNTS['cooldown_skip'] += 1
                    logger.info('[PAPER_DEBUG] cooldown active for %s (%.0fs left) -> skip', addr, cd-(now-last))
                else:
                    # ANOMALY SCORE SYSTEM (0-10)
                    # Validated: Iran strike 3d $61k->9.5, Liverpool 15d $56k->5.0
                    anomaly_score = 0.0
                    first_seen = wallet.get("first_seen", 0)
                    wallet_age_days = (time.time() - float(first_seen)) / 86400 if first_seen else 9999
                    if wallet_age_days < 1:    anomaly_score += 4.0
                    elif wallet_age_days < 7:   anomaly_score += 3.0
                    elif wallet_age_days < 30:  anomaly_score += 1.5

                    trade_size = float(trade_record.get("size_usd", 0) or 0)
                    avg_bet    = float(wallet.get("avg_bet_size", 0) or 50)
                    if trade_size >= 10_000:    anomaly_score += 2.0
                    elif trade_size >= 2_000:   anomaly_score += 1.0
                    elif trade_size >= 500:     anomaly_score += 0.5

                    num_markets = int(wallet.get("markets_traded", wallet.get("num_markets_traded", wallet.get("market_count", 0))) or 0)
                    if 0 < num_markets <= 2:    anomaly_score += 2.0
                    elif 0 < num_markets <= 5:  anomaly_score += 0.5

                    _cv = getattr(paper, "_volume_cache", {}).get(trade_record.get("market_slug",""))
                    if _cv:
                        if 0 < _cv[0] < 20_000:  anomaly_score += 1.5
                        elif _cv[0] < 50_000:     anomaly_score += 0.5

                    if avg_bet > 100 and trade_size >= avg_bet * 5:
                        anomaly_score += 1.0

                    anomaly_score     = round(anomaly_score, 1)
                    is_insider_signal = anomaly_score >= 5.0
                    is_super_insider  = anomaly_score >= 7.0
                    trade_record["anomaly_score"] = anomaly_score

                    if anomaly_score >= 3.0:
                        logger.info("[ANOMALY] score=%.1f age=%.1fd size=$%.0f mkts=%d slug=%s",
                            anomaly_score, wallet_age_days, trade_size, num_markets,
                            trade_record.get("market_slug",""))
                    if is_super_insider:
                        alerts.send_telegram_sync("SUPER INSIDER score=" + str(anomaly_score) + " | " + str(trade_record.get("market_slug","")) + " age=" + str(round(wallet_age_days,1)) + "d $" + str(int(trade_size)))
                    elif is_insider_signal:
                        alerts.send_telegram_sync("INSIDER score=" + str(anomaly_score) + " | " + str(trade_record.get("market_slug","")) + " age=" + str(round(wallet_age_days,1)) + "d $" + str(int(trade_size)))

                    # ── Conviction multiplier ─────────────────────────────
                    # Wallet betting 3x+ their average = high conviction
                    # Copy at full Kelly. Normal size = half Kelly.
                    if avg_bet > 100 and trade_size >= avg_bet * 3:
                        # Scale conviction multiplier based on anomaly score tier
                        if is_super_insider:
                            _conv_mult = 3.0   # SUPER_INSIDER: 3x sizing
                        elif is_insider_signal:
                            _conv_mult = 2.0   # INSIDER: 2x sizing
                        else:
                            _conv_mult = 1.5   # High conviction normal wallet
                        sizing["size_usd"] = min(
                            sizing["size_usd"] * _conv_mult,
                            float(getattr(config, "MAX_PAPER_TRADE_USD", 50)) * 3
                        )
                        logger.info("[CONVICTION] score=%.1f mult=%.1fx trade=%.0fx_avg size=$%.0f",
                                    anomaly_score, _conv_mult, trade_size/avg_bet, sizing["size_usd"])
                    else:
                        pass  # normal sizing from Kelly

                    # ── Consensus filter: only trade when 2+ wallets agree ──
                    # EXCEPTION: anomaly >=3 (SUSPICIOUS+) bypasses consensus
                    # rationale: fresh wallets with large bets don't wait for confirmation
                    min_consensus = int(getattr(config, 'MIN_CONSENSUS_WALLETS', 1))
                    if min_consensus > 1 and anomaly_score < 3.0:
                        slug_check  = trade_record.get("market_slug", "")
                        out_check   = trade_record.get("outcome", "")
                        side_check  = trade_record.get("side", "")
                        try:
                            consensus_count = paper.conn.execute(
                                """
                                SELECT COUNT(DISTINCT wallet_address) as n
                                FROM paper_positions
                                WHERE market_slug=? AND outcome=? AND side=?
                                AND (status='open' OR opened_at > ?)
                                """,
                                (slug_check, out_check, side_check, time.time() - 3600),
                            ).fetchone()["n"]
                        except Exception as _poll_err:
                            consensus_count = 0
                        if consensus_count < (min_consensus - 1):
                            DECISION_COUNTS['cooldown_skip'] += 1
                            logger.info('[PAPER_DEBUG] consensus skip %s: only %d/%d wallets',
                                        slug_check, consensus_count + 1, min_consensus)
                            continue

                    # ── Signal freshness / entry timing ──────────────────────────
                    # Research: "if price moved >10% since whale entry, edge is gone"
                    # Only applies to non-insider consensus trades (insiders are time-sensitive)
                    _whale_entry = float(trade_record.get("price", 0) or 0)
                    _signal_age_min = 0
                    _ckey_fr = (
                        str(trade_record.get("market_slug", "")) + "|"
                        + str(trade_record.get("outcome", "")) + "|"
                        + str(trade_record.get("side", ""))
                    )
                    if _ckey_fr in CONSENSUS_TRACKER:
                        _signal_age_min = (time.time() - CONSENSUS_TRACKER[_ckey_fr].get("ts", time.time())) / 60
                        # Store first_price when signal first arrived
                        if "first_price" not in CONSENSUS_TRACKER[_ckey_fr]:
                            CONSENSUS_TRACKER[_ckey_fr]["first_price"] = _whale_entry

                    if _signal_age_min > 0 and _whale_entry > 0 and anomaly_score < 5.0:
                        _first_price = CONSENSUS_TRACKER.get(_ckey_fr, {}).get("first_price", _whale_entry)
                        _price_drift = (_whale_entry - _first_price) / _first_price if _first_price > 0 else 0

                        # Age penalty
                        _age_factor = 1.0
                        if _signal_age_min > 120:
                            _age_factor = 0.0    # >2 hours = skip
                        elif _signal_age_min > 60:
                            _age_factor = 0.5
                        elif _signal_age_min > 30:
                            _age_factor = 0.75

                        # Drift penalty
                        _drift_factor = 1.0
                        if _price_drift > 0.25:
                            _drift_factor = 0.0
                        elif _price_drift > 0.15:
                            _drift_factor = 0.4
                        elif _price_drift > 0.10:
                            _drift_factor = 0.7

                        _freshness = round(_age_factor * _drift_factor, 3)
                        if _freshness < 0.3:
                            logger.info(
                                "[STALE_SIGNAL] age=%.0fmin drift=%.1f%% freshness=%.2f → skip %s",
                                _signal_age_min, _price_drift * 100, _freshness,
                                trade_record.get("market_slug", "")
                            )
                            DECISION_COUNTS["cooldown_skip"] += 1
                            continue
                        elif _freshness < 1.0:
                            sizing["size_usd"] = round(sizing["size_usd"] * _freshness, 2)
                            logger.info(
                                "[FRESHNESS] age=%.0fmin freshness=%.2f → scaling size to $%.0f",
                                _signal_age_min, _freshness, sizing["size_usd"]
                            )

                    # ── Wallet decay detection ─────────────────────────────────────
                    # If wallet lifetime win rate > 60% but recent 20 trades < 45%
                    # they've lost their edge — skip or reduce
                    _lifetime_wr = float(wallet.get("win_rate", 0) or 0)
                    _recent_wr   = float(wallet.get("recent_win_rate", _lifetime_wr) or _lifetime_wr)
                    _recent_n    = int(wallet.get("recent_trades_n", 0) or 0)
                    if _recent_n >= 10:
                        if _lifetime_wr > 0.60 and _recent_wr < 0.45:
                            logger.info(
                                "[DECAY] wallet lifetime=%.0f%% recent=%.0f%% n=%d → SKIP (lost edge)",
                                _lifetime_wr * 100, _recent_wr * 100, _recent_n
                            )
                            DECISION_COUNTS["cooldown_skip"] += 1
                            continue
                        elif _lifetime_wr > 0.55 and _recent_wr < 0.48:
                            _decay_mult = 0.5
                            sizing["size_usd"] = round(sizing["size_usd"] * _decay_mult, 2)
                            logger.info(
                                "[DECAY] moderate decay lifetime=%.0f%% recent=%.0f%% → size halved $%.0f",
                                _lifetime_wr * 100, _recent_wr * 100, sizing["size_usd"]
                            )
                        elif _recent_wr > _lifetime_wr + 0.10 and _recent_n >= 15:
                            sizing["size_usd"] = min(
                                round(sizing["size_usd"] * 1.3, 2),
                                float(getattr(config, "MAX_PAPER_TRADE_USD", 50)) * 2
                            )
                            logger.info(
                                "[DECAY] improving wallet lifetime=%.0f%%→recent=%.0f%% → +30%% size $%.0f",
                                _lifetime_wr * 100, _recent_wr * 100, sizing["size_usd"]
                            )

                    logger.info("[DEBUG_PREOPEN] about to call open_position slug=%s side=%s size=$%.0f", trade_record.get("market_slug", ""), trade_record.get("side", ""), sizing["size_usd"])
                    pos_id = paper.open_position(wallet, trade_record, sizing)
                    if pos_id and int(pos_id) > 0:
                        LAST_PAPER_TS[addr] = now
                        DECISION_COUNTS['opened'] += 1
                        paper_size = sizing["size_usd"]
                        trade_record["paper_size"] = paper_size
                    else:
                        reason = getattr(paper, 'last_skip_reason', None) or 'unknown_skip'
                        logger.info('[PAPER_DEBUG] open_position skipped reason=%s', reason)
            else:
                DECISION_COUNTS['kelly_skip'] += 1
                logger.info("[PAPER_DEBUG] sizing None -> skip (kelly/floor)")
        else:
            logger.info("[PAPER_DEBUG] paper trader is OFF (paper=None)")
        # Send alerts
        # Send alerts
        # Rich paper action object for Telegram (opened vs skipped)
        paper_action = None
        if paper:
            paper_action = {
                "status": "OPENED" if paper_size else "SKIPPED",
                "size_usd": float(paper_size or 0),
                "bankroll": float(getattr(paper, "bankroll", 0.0)),
            }

        # Only alert on actual opens (not every detection) and only if configured
        _notify_exec = getattr(config, 'TELEGRAM_NOTIFY_EXECUTIONS', True)
        _notify_detect = getattr(config, 'TELEGRAM_NOTIFY_DETECTIONS', False)
        _should_alert = (_notify_exec and paper_action and paper_action.get('status') == 'OPENED') or                         (_notify_detect and (not paper_action or paper_action.get('status') != 'OPENED'))

        if _should_alert:
            msg = alerts.format_new_trade_alert(wallet, trade_record, paper_action)
            try:
                alerts.send_telegram_sync(msg) if can_send_alert() else logger.info('telegram alert suppressed')
            except Exception as e:
                logger.warning(f"[telegram] send trade alert failed: {e!r}")



    global LAST_DECISION_LOG_TS
    if time.time() - LAST_DECISION_LOG_TS > 60:
        log_decisions_summary()
        LAST_DECISION_LOG_TS = time.time()
    # ── change-driven report (C) ────────────────────────────────
    global LAST_REPORT_STATE, LAST_REPORT_SENT_TS
    if paper:
        cur = report_state(conn, paper)
        throttle = int(getattr(config, 'REPORT_THROTTLE_SEC', 30))
        if should_send_report(LAST_REPORT_STATE, cur) and (time.time() - LAST_REPORT_SENT_TS) >= throttle:
            alerts.send_telegram_sync(build_portfolio_report(conn, paper))
            LAST_REPORT_STATE = cur
            LAST_REPORT_SENT_TS = time.time()
    return new_trades





async def check_resolutions(
    client: httpx.AsyncClient,
    conn,
    paper: PaperTrader,
):
    """Check if any open paper positions have resolved."""
    open_positions = db.get_open_positions(conn)
    if not open_positions:
        return

    resolved = {}
    checked_slugs = set()

    for pos in open_positions:
        slug = pos["market_slug"]
        if slug in checked_slugs:
            continue
        checked_slugs.add(slug)

        market = await poly_api.fetch_market_by_slug(client, slug)
        if not market:
            continue

        if market.get("closed") or market.get("resolved"):
            result = market.get("result", market.get("outcome"))
            if result is not None:
                try:
                    resolved[slug] = float(result)
                except (ValueError, TypeError):
                    if result in ("Yes", "yes", True, "1"):
                        resolved[slug] = 1.0
                    elif result in ("No", "no", False, "0"):
                        resolved[slug] = 0.0

    if not resolved:
        return

    closed = paper.check_resolutions(resolved) or []

    # Label learning rows with realized pnl/result
    try:
        for c in closed:
            slug = c.get("market_slug")
            pnl = float(c.get("pnl", 0))
            # result is exit_price (0/1-ish) if available; use None if missing
            result = None
            try:
                result = float(c.get("exit_price"))
            except Exception as _poll_err:
                pass
            conn.execute(
                """
                UPDATE trade_features
                   SET resolved_at=?,
                       result=?,
                       pnl=?
                 WHERE market_slug=?
                   AND resolved_at IS NULL
                """,
                (time.time(), result, pnl, slug),
            )
        conn.commit()
    except Exception as e:
        logger.warning("[learn] trade_features label update failed: %r", e)

    for c in closed:
        pnl = float(c.get("pnl", 0))
        outcome_tag = "✅ WIN" if pnl >= 0 else "❌ LOSS"
        market_q = (c.get("market_question") or "")[:160]
        side = c.get("side", "?")
        outcome = c.get("outcome", "?")
        size = float(c.get("size_usd", 0))
        entry = c.get("entry_price", "?")
        exitp = c.get("exit_price", "?")
        bankroll = c.get("bankroll", 0)

        text = (
            f"{outcome_tag}  P&L ${pnl:+.2f}\n"
            f"{market_q}\n"
            f"{side} {outcome} | Size ${size:,.0f}\n"
            f"Entry {entry} -> Exit {exitp}\n"
            f"Bankroll: ${bankroll:,.2f}"
        )

        try:
            alerts.send_telegram_sync(text)
        except Exception as e:
            logger.warning(f"[telegram] send win/loss failed: {e!r}")


async def run_monitor(




    max_wallets: int,
    interval: int,
    min_size: float,
    extra_wallets: list[str],
    bankroll: float,
    no_paper: bool,
    db_path: str,
):
    global RUNNING

    conn = db.get_connection(db_path)
    db.init_db(conn)

    # Load wallets from DB (from scanner output)
    wallets = db.get_active_wallets(conn, limit=max_wallets)

    # Add any manually specified wallets
    for addr in extra_wallets:
        if not any(w["address"].lower() == addr.lower() for w in wallets):
            wallets.append({
                "address": addr,
                "username": addr[:10],
                "alpha_score": 0,
                "pnl": 0,
                "win_rate": 0.5,
                "profit_factor": 1.0,
            })

    if not wallets:
        console.print(
            "[red]No wallets to monitor.[/] "
            "Run [cyan]poly_alpha_scanner.py[/] first, or use --wallet to add one."
        )
        return

    paper = None if no_paper else PaperTrader(conn, bankroll)
    if paper:
        asyncio.create_task(telegram_command_loop(conn, paper))  # /status /report
        asyncio.create_task(wallet_rescan_loop(conn))            # weekly wallet refresh

    console.print(Panel.fit(
        f"[bold cyan]Polymarket Alpha Monitor[/]\n"
        f"Watching {len(wallets)} wallets · "
        f"Poll every {interval}s · "
        f"Min trade ${min_size:,.0f}\n"
        f"Paper trading: {'[green]ON[/]' if paper else '[red]OFF[/]'}"
        + (f" · Bankroll: ${paper.bankroll:,.0f}" if paper else ""),
        border_style="cyan",
    ))

    recent_trades = []
    poll_count = 0
    last_poll = 0.0


    next_maintenance_ts = time.time() + MAINTENANCE_EVERY_SEC
    async with httpx.AsyncClient(
        headers={"User-Agent": "PolyAlphaMonitor/1.0"},
        follow_redirects=True,
    ) as client:

        with Live(
            build_dashboard(wallets, recent_trades, paper, poll_count, last_poll),
            console=console,
            refresh_per_second=1,
        ) as live:
              while RUNNING:
                  poll_count += 1
                  if poll_count % 5 == 0:
                      print(f"[heartbeat] polls={poll_count} wallets={len(wallets)}", file=sys.stderr)

                  last_poll = time.time()

                  for wallet in wallets:
                      if not RUNNING:
                          break
                      try:
                          new = await poll_wallet(client, conn, wallet, paper, min_size)
                          recent_trades.extend(new)
                      except Exception as _poll_err:
                          logger.warning("[POLL_ERR] wallet=%s err=%r", wallet.get("address","?"), _poll_err)

                  recent_trades = recent_trades[-50:]

                  # --- Paper auto-close to prevent cap deadlock ---
                  if paper:
                      try:
                          n_closed = paper.auto_close_positions()
                          if n_closed:
                              logger.info('[PAPER_DEBUG] auto_closed=%d', n_closed)
                      except Exception as _poll_err:
                          logger.warning("[POLL_ERR] wallet=%s err=%r", wallet.get("address","?"), _poll_err)


                  if paper and poll_count % 10 == 0:
                      try:
                          await check_resolutions(client, conn, paper)
                      except Exception as _poll_err:
                          logger.warning("[POLL_ERR] wallet=%s err=%r", wallet.get("address","?"), _poll_err)

                  conn.commit()

                  # ── Autonomous maintenance (timestamp-based)
                  now = time.time()
                  if now >= next_maintenance_ts:
                      try:
                          pruned, added, active_n = await run_maintenance(conn, max_wallets)
                          conn.commit()
                          wallets = db.get_active_wallets(conn, limit=max_wallets)
                          if pruned or added:
                              text = (
                                  f"🧹 Wallet maintenance\n"
                                  f"Pruned: {len(pruned)} (>{DEAD_WALLET_DAYS}d inactive)\n"
                                  f"Added: {len(added)}\n"
                                  f"Active watching: {len(wallets)}/{max_wallets}"
                              )
                              try:
                                  alerts.send_telegram_sync(text)
                              except Exception as _poll_err:
                                  logger.warning("[POLL_ERR] wallet=%s err=%r", wallet.get("address","?"), _poll_err)
                      except Exception as _poll_err:
                          logger.warning("[POLL_ERR] wallet=%s err=%r", wallet.get("address","?"), _poll_err)
                      next_maintenance_ts = now + MAINTENANCE_EVERY_SEC


                  live.update(
                      build_dashboard(wallets, recent_trades, paper, poll_count, last_poll)
                  )

                  if poll_count % 20 == 0:
                      msg = f"🟦 Polymarket bot alive: polls={poll_count}"
                      try:
                          telegram_heartbeat(msg)
                      except Exception as e:
                          logger.exception("telegram_heartbeat failed: %s", e)

                  for _ in range(interval * 2):
                      if not RUNNING:
                          break
                      await asyncio.sleep(0.5)


    # Final summary
    if paper:
        s = paper.get_summary()
        console.print("\n[bold]Paper Trading Final Summary:[/]")
        console.print(f"  Bankroll: ${s['bankroll']:,.2f}")
        console.print(f"  Total P&L: ${s['total_pnl']:+,.2f}")
        console.print(f"  Return: {s['total_return_pct']:+.2f}%")
        console.print(f"  Trades: {s['total_trades']} "
                       f"(W:{s['wins']} / L:{s['losses']})")
        console.print(f"  Win rate: {s['win_rate']:.1%}")

    conn.close()


@click.command()
@click.option("--max-wallets", default=config.MAX_WATCH_WALLETS,
              help="Max wallets to monitor")
@click.option("--interval", default=config.POLL_INTERVAL,
              help="Poll interval in seconds")
@click.option("--min-size", default=config.MIN_TRADE_SIZE,
              help="Min trade size in USD to alert on")
@click.option("--wallet", "extra_wallets", multiple=True,
              help="Additional wallet address(es) to watch")
@click.option("--bankroll", default=config.STARTING_BANKROLL,
              help="Starting paper bankroll")
@click.option("--no-paper", is_flag=True, help="Disable paper trading")
@click.option("--db", "db_path", default=config.DB_PATH,
              help="SQLite database path")
def main(max_wallets, interval, min_size, extra_wallets, bankroll, no_paper, db_path):
    asyncio.run(run_monitor(
        max_wallets, interval, min_size, list(extra_wallets),
        bankroll, no_paper, db_path,
    ))


_start_notice_file = Path("/tmp/polymarket_bot_start_notice")

def send_start_notice_once_per_hour() -> None:
    try:
        if _start_notice_file.exists():
            age = time.time() - _start_notice_file.stat().st_mtime
            if age < 3600:
                print(f"[telegram] start notice skipped (age={int(age)}s)", file=sys.stderr)
                return

        print("[telegram] start notice sending", file=sys.stderr)
        telegram_heartbeat("✅ Polymarket bot started (systemd) — heartbeat OK.")
        _start_notice_file.touch()
    except Exception as _poll_err:
        pass

if __name__ == "__main__":
    send_start_notice_once_per_hour()
    main()
